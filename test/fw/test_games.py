"""games.c on its device path: Sudoku's rules, Vortex's physics, Klondike's legality, Snake, keys and saves."""
import ctypes as C
import math
import mmap
import random
from types import SimpleNamespace

import pytest

SEED = 2166136261
SUD, BEST, SOL, SNK = 0x31445553, 0x31585456, 0x314c4f53, 0x314b4e53
CENTER, PREV, NEXT, PLAY, RETURN, BACK_BTN, FWD_BTN = 218, 172, 173, 171, 170, 222, 223
CHOOSER, SUDOKU, VORTEX, SOLITAIRE, SNAKE = range(5)
AIM, RUNNING, OVER = range(3)
RUN, HELD, MENU = range(3)
UP = 64
STOCK, WASTE, FOUND, TABLEAU, PILES = 0, 1, 2, 6, 13
R_PAD, MISS = 118, 128
V0, VSTEP, VMAX = 2 << 16, 1 << 14, 9 << 15
EDGE = (R_PAD - 4) * 256
r = C.byref


class Sudoku(C.Structure):
    _fields_ = [('magic', C.c_uint), ('cell', C.c_ubyte * 81), ('cur', C.c_ubyte), ('pad', C.c_ubyte * 2), ('sum', C.c_uint)]


class Vortex(C.Structure):
    _fields_ = [*((k, C.c_int) for k in 'state lives level score pa x y vx vy'.split()),
                ('brick', C.c_ushort * 3), ('pad', C.c_ushort)]


class Sol(C.Structure):
    _fields_ = [('magic', C.c_uint), ('card', (C.c_ubyte * 24) * 13), ('len', C.c_ubyte * 13), ('cur', C.c_ubyte),
                ('pad', C.c_ubyte * 2), ('sum', C.c_uint)]


class Snake(C.Structure):
    _fields_ = [('body', C.c_ushort * 400), ('tail', C.c_ushort), ('len', C.c_ushort), ('food', C.c_ushort),
                ('dir', C.c_ubyte), ('turned', C.c_ubyte), ('state', C.c_ubyte), ('next', C.c_byte), ('score', C.c_int)]


class Best(C.Structure):
    _fields_ = [('magic', C.c_uint), ('score', C.c_uint), ('sum', C.c_uint)]


class Game(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page list view sheet'.split()),
                *((k, C.c_uint) for k in 'timer frame at acc'.split()),
                *((k, C.c_int) for k in 'h screen target picking pick confirm paused choice carry dirty'.split()),
                ('sud', Sudoku), ('vx', Vortex), ('best', Best), ('sol', Sol), ('save', C.c_uint), ('pop_at', C.c_uint),
                ('pop_ring', C.c_int), ('pop_seg', C.c_int), ('trail', (C.c_int * 2) * 4), ('record', C.c_int),
                ('sn_best', Best), ('sn', Snake)]


def test_structs_are_the_saves_sizes():
    assert (C.sizeof(Sudoku), C.sizeof(Sol), C.sizeof(Best), C.sizeof(Vortex), C.sizeof(Snake)) == (92, 336, 12, 44, 816)


def api(fw):
    ptr, i, u = C.c_void_p, C.c_int, C.c_uint
    fns = {'games_ok': (i, ptr, u, u), 'games_seal': (None, ptr, u, u), 'hash_bytes': (u, u, ptr, u),
           'sud_puzzles': (i,), 'sud_puzzle': (None, i, ptr), 'sud_new': (None, ptr), 'sud_set': (i, ptr, i, i),
           'sud_conflict': (i, ptr, i), 'sud_done': (i, ptr), 'vortex_new': (None, ptr), 'vortex_launch': (None, ptr),
           'vortex_step': (None, ptr, i), 'sol_new': (None, ptr), 'sol_draw': (i, ptr), 'sol_move': (i, ptr, i, i),
           'sol_home': (i, ptr, i), 'sol_won': (i, ptr), 'sol_finish': (None, ptr), 'snake_new': (None, ptr),
           'snake_turn': (None, ptr, i), 'snake_ms': (i, ptr), 'snake_step': (i, ptr), 'games_live': (i,),
           'games_open': (i, ptr, ptr), 'games_key': (i, ptr, u), 'games__vsin': (i, i), 'games__vcos': (i, i),
           'games__vangle': (i, i, i), 'games__vsqrt': (u, C.c_ulonglong), 'games__radius': (i, i, i),
           'games__place': (None, ptr), 'games__head': (None, ptr, i), 'games__level': (None, ptr),
           'games__sol_fits': (i, ptr, i, i), 'games__sud_started': (i, ptr), 'games__deal': (None, ptr, i),
           'games__sn_food': (None, ptr), 'games__closed': (i, ptr, ptr), 'games__flush': (None, i),
           'games__save_due': (i, ptr), 'games__show': (i, ptr), 'games__frame': (i, ptr), 'games__leave': (i, ptr)}
    return SimpleNamespace(**{k.replace('games__', ''): fw.fn(k, v[0], *v[1:]) for k, v in fns.items()})


def seed(fw, n):
    fw.var('playback__rng', C.c_uint).value = n or 1


def fnv1a(data, h=SEED):
    for b in data:
        h = (h ^ b) * 16777619 & 0xffffffff
    return h


libc = C.CDLL(None)
libc.malloc.restype, libc.malloc.argtypes = C.c_void_p, [C.c_size_t]
libc.free.argtypes = [C.c_void_p]


class Exact:
    """data in a malloc of exactly its size, so the sanitizer sees a read or write past it."""
    def __init__(self, data):
        self.n, self.p = len(data), libc.malloc(len(data) or 1)
        C.memmove(self.p, bytes(data), len(data))

    def __enter__(self):
        return self.p

    def __exit__(self, *_):
        self.data = C.string_at(self.p, self.n)
        libc.free(self.p)


def solve(grid, limit=2):
    """grid's solutions, at most limit, by candidates: most constrained cell first."""
    g = list(grid)
    row, col, box = [0] * 9, [0] * 9, [0] * 9
    for i, v in enumerate(g):
        if v:
            b, y, x = 1 << v, i // 9, i % 9
            if (row[y] | col[x] | box[y // 3 * 3 + x // 3]) & b:
                return []
            row[y] |= b
            col[x] |= b
            box[y // 3 * 3 + x // 3] |= b
    found = []

    def go():
        best = None
        for i in range(81):
            if not g[i]:
                y, x = divmod(i, 9)
                m = ~(row[y] | col[x] | box[y // 3 * 3 + x // 3]) & 0x3fe
                if not m:
                    return
                if best is None or bin(m).count('1') < bin(best[1]).count('1'):
                    best = (i, m)
        if best is None:
            found.append(list(g))
            return
        i, m = best
        y, x = divmod(i, 9)
        k = y // 3 * 3 + x // 3
        for v in range(1, 10):
            b = 1 << v
            if m & b and len(found) < limit:
                g[i] = v
                row[y] |= b
                col[x] |= b
                box[k] |= b
                go()
                row[y] ^= b
                col[x] ^= b
                box[k] ^= b
                g[i] = 0
    go()
    return found


def peers(i):
    y, x = divmod(i, 9)
    same = {y * 9 + k for k in range(9)} | {k * 9 + x for k in range(9)}
    same |= {(y // 3 * 3 + a) * 9 + x // 3 * 3 + b for a in range(3) for b in range(3)}
    return same - {i}


SOLVED = [(y * 3 + y // 3 + x) % 9 + 1 for y in range(9) for x in range(9)]  # the classic shifted Latin square


# FNV-1a and the saves' seal

def test_hash_bytes_is_fnv1a_by_its_published_vectors(fw):
    g = api(fw)
    for data, want in ((b'', 0x811c9dc5), (b'a', 0xe40c292c), (b'foobar', 0xbf9cf968)):
        assert g.hash_bytes(SEED, data, len(data)) == want
    assert g.hash_bytes(g.hash_bytes(SEED, b'foo', 3), b'bar', 3) == 0xbf9cf968  # it chains


def test_hash_bytes_hostile_sweep(fw):
    g, rng = api(fw), random.Random(1)
    for _ in range(2000):
        data = rng.randbytes(rng.randrange(0, 64))
        with Exact(data) as p:
            assert g.hash_bytes(SEED, p, len(data)) == fnv1a(data)


@pytest.mark.parametrize('size', (12, 92, 336))
def test_a_sealed_save_reads_back_and_any_flipped_byte_does_not(fw, size):
    g = api(fw)
    raw = (C.c_ubyte * size)(*random.Random(size).randbytes(size))
    g.games_seal(raw, size, SOL)
    assert bytes(raw)[:4] == SOL.to_bytes(4, 'little')
    assert bytes(raw)[-4:] == fnv1a(bytes(raw)[:-4]).to_bytes(4, 'little')
    assert g.games_ok(raw, size, SOL)
    assert not g.games_ok(raw, size, SUD)
    for i in range(size):
        raw[i] ^= 1
        assert not g.games_ok(raw, size, SOL), i
        raw[i] ^= 1


def test_games_ok_a_zeroed_short_read_is_no_save(fw):
    g = api(fw)
    for size in (12, 92, 336):
        assert not g.games_ok((C.c_ubyte * size)(), size, SUD)


def test_games_ok_reads_only_its_size(fw):
    g = api(fw)
    buf = (C.c_ubyte * 40)()
    g.games_seal(buf, 12, BEST)
    assert bytes(buf)[12:] == bytes(28)
    buf[12] = 0xff
    assert g.games_ok(buf, 12, BEST)


def test_games_ok_hostile_sweep(fw):
    g, rng = api(fw), random.Random(2)
    accepted = 0
    for k in range(2000):
        size = rng.choice((12, 92, 336))
        data = bytearray(rng.randbytes(size))
        if k % 2:
            data[:4] = SUD.to_bytes(4, 'little')
        if k % 4 == 3:
            data[-4:] = fnv1a(data[:-4]).to_bytes(4, 'little')
        if k % 16 == 15:
            data[rng.randrange(size)] ^= 1 << rng.randrange(8)
        want = data[:4] == SUD.to_bytes(4, 'little') and data[-4:] == fnv1a(data[:-4]).to_bytes(4, 'little')
        with Exact(data) as p:
            assert bool(g.games_ok(p, size, SUD)) == want
        accepted += want
    assert 350 < accepted < 500  # three in four of the sealed ones


# Sudoku

def test_the_bundled_puzzles_each_have_one_solution(fw):
    g = api(fw)
    assert g.sud_puzzles() == 24
    out = (C.c_ubyte * 81)()
    seen = set()
    for k in range(24):
        g.sud_puzzle(k, out)
        assert max(out) <= 9 and sum(1 for v in out if v) >= 17, k  # 17 is the least a unique Sudoku can have
        assert len(solve(out)) == 1, k
        seen.add(bytes(out))
    assert len(seen) == 24


def test_a_new_sudoku_is_a_shuffled_bundled_one_with_one_solution(fw):
    g = api(fw)
    counts = set()
    out = (C.c_ubyte * 81)()
    for k in range(24):
        g.sud_puzzle(k, out)
        counts.add(sum(1 for v in out if v))
    for n in range(1, 21):
        seed(fw, n * 2654435761 % 2**32)
        s = Sudoku()
        memset = (C.c_ubyte * C.sizeof(s)).from_buffer(s)
        memset[:] = [0xaa] * C.sizeof(s)
        g.sud_new(r(s))
        assert s.magic == 0 and s.cur == 0 and s.sum == 0
        assert all(c == 0 or (c & 16 and 1 <= c & 15 <= 9) and c < 32 for c in s.cell)
        assert sum(1 for c in s.cell if c) in counts
        assert len(solve([c & 15 for c in s.cell])) == 1, n


def test_sud_set_a_clue_stays_and_bounds_hold(fw):
    g = api(fw)
    s = Sudoku()
    s.cell[40] = 16 | 7
    assert not g.sud_set(r(s), 40, 3) and s.cell[40] == 23
    assert not g.sud_set(r(s), 40, 0) and s.cell[40] == 23
    for i, v in ((-1, 1), (81, 1), (0, -1), (0, 10)):
        before = bytes(s)
        assert not g.sud_set(r(s), i, v) and bytes(s) == before, (i, v)
    for v in range(10):
        assert g.sud_set(r(s), 0, v) and s.cell[0] == v
    assert g.sud_set(r(s), 80, 9) and s.cell[80] == 9  # the last cell
    assert g.sud_set(r(s), 80, 0) and s.cell[80] == 0  # blanked


def test_sud_conflict_is_the_rule_on_random_grids(fw):
    g, rng = api(fw), random.Random(3)
    for trial in range(60):
        s = Sudoku()
        for i in range(81):
            s.cell[i] = rng.choice((0, 0, rng.randint(1, 9), rng.randint(1, 9) | 16))
        d = [c & 15 for c in s.cell]
        for i in range(81):
            assert bool(g.sud_conflict(r(s), i)) == bool(d[i] and any(d[p] == d[i] for p in peers(i))), (trial, i)


@pytest.mark.parametrize('i, peer', ((0, 8), (0, 72), (0, 20), (80, 72), (80, 8), (80, 60), (40, 30), (40, 50), (26, 8), (54, 74)))
def test_sud_conflict_the_grid_s_corners_and_box_edges(fw, i, peer):
    g = api(fw)
    s = Sudoku()
    s.cell[i], s.cell[peer] = 5, 5 | 16
    assert g.sud_conflict(r(s), i) and g.sud_conflict(r(s), peer)
    s.cell[peer] = 6 | 16
    assert not g.sud_conflict(r(s), i)


def test_sud_conflict_two_blanks_are_no_conflict(fw):
    g = api(fw)
    s = Sudoku()
    assert not any(g.sud_conflict(r(s), i) for i in range(81))


def test_sud_done_needs_every_cell_and_no_repeat(fw):
    g = api(fw)
    s = Sudoku()
    assert not g.sud_done(r(s))
    s.cell[:] = SOLVED
    assert len(solve(SOLVED)) == 1 and g.sud_done(r(s))
    s.cell[30] |= 16  # a clue counts as its digit
    assert g.sud_done(r(s))
    s.cell[80] = 0
    assert not g.sud_done(r(s))
    s.cell[:] = SOLVED
    s.cell[0], s.cell[1] = s.cell[1], s.cell[1]
    assert not g.sud_done(r(s))


def test_sud_started_is_an_entry_not_a_clue(fw):
    g = api(fw)
    s = Sudoku()
    assert not g.sud_started(r(s))
    s.cell[0] = 16 | 3
    assert not g.sud_started(r(s))
    s.cell[80] = 4
    assert g.sud_started(r(s))


# Vortex: integer sine, arctangent and square root

def test_the_sine_table_is_a_quarter_of_sin(fw):
    table = fw.var('games__quarter', C.c_ushort * 257)
    for i, v in enumerate(table):
        assert abs(v - min(65535, 65536 * math.sin(i * math.tau / 1024))) <= 1, i


def test_the_arctangent_table_is_atan(fw):
    table = fw.var('games__atans', C.c_ubyte * 257)
    for i, v in enumerate(table):
        assert abs(v - math.atan(i / 256) * 1024 / math.tau) <= 0.75, i


def test_vsin_and_vcos_cover_every_angle_with_wraparound(fw):
    g = api(fw)
    for a in range(-2048, 2048, 3):
        t = a * math.tau / 1024
        assert abs(g.vsin(a) - 65536 * math.sin(t)) <= 2, a
        assert abs(g.vcos(a) - 65536 * math.cos(t)) <= 2, a
    assert (g.vsin(0), g.vsin(256), g.vsin(512), g.vsin(768)) == (0, 65535, 0, -65535)
    assert g.vsin(1024 + 100) == g.vsin(100) and g.vsin(-1) == -g.vsin(1)


def test_vangle_on_the_axes_and_at_every_angle(fw):
    g = api(fw)
    assert (g.vangle(0, 1), g.vangle(1, 0), g.vangle(0, -1), g.vangle(-1, 0)) == (0, 256, 512, 768)
    assert g.vangle(0, 0) == 0
    assert (g.vangle(5, 5), g.vangle(5, -5), g.vangle(-5, -5), g.vangle(-5, 5)) == (128, 384, 640, 896)  # the diagonals
    for scale in (300, 30000, (1 << 23) - 1):
        for a in range(0, 1024, 5):
            t = a * math.tau / 1024
            got = g.vangle(round(scale * math.sin(t)), round(scale * math.cos(t)))
            assert abs((got - a + 512) % 1024 - 512) <= 2, (scale, a)


@pytest.mark.parametrize('n', (0, 1, 2, 3, 4, 8, 15, 16, 17, 10**6, 2**32 - 1, 2**32, 2**62, 2**63, 2**64 - 1))
def test_vsqrt_at_powers_and_limits(fw, n):
    assert api(fw).vsqrt(n) == math.isqrt(n)


def test_vsqrt_is_the_floor_either_side_of_each_square(fw):
    g, rng = api(fw), random.Random(4)
    for _ in range(600):
        root = rng.randrange(1, 1 << rng.randrange(1, 32))
        for n in (root * root - 1, root * root, root * root + 1):
            assert g.vsqrt(n) == math.isqrt(n), n


def test_radius_is_distance_in_256ths_of_a_pixel(fw):
    g = api(fw)
    assert g.radius(3 << 16, 4 << 16) == 5 * 256
    assert g.radius(-3 << 16, 4 << 16) == 5 * 256
    assert g.radius(0, 0) == 0
    assert g.radius(-(1 << 16), 0) == 256
    assert g.radius(150 << 16, 150 << 16) == math.isqrt(2 * (150 * 256) ** 2)


# Vortex: the ball

def fresh(g, state=RUNNING):
    v = Vortex()
    g.vortex_new(r(v))
    v.state = state
    return v


def at(angle, px):
    t = angle * math.tau / 1024
    return round(px * 65536 * math.cos(t)), round(px * 65536 * math.sin(t))


def heading(v):
    return math.atan2(v.vy, v.vx) * 1024 / math.tau


def off(a, b):
    return (a - b + 512) % 1024 - 512


def test_a_new_game(fw):
    g = api(fw)
    v = Vortex()
    v.score, v.level, v.vx = 9, 9, 9
    g.vortex_new(r(v))
    assert (v.state, v.lives, v.level, v.score, v.pa, v.vx, v.vy) == (AIM, 3, 0, 0, 768, 0, 0)
    assert list(v.brick) == [0xffff] * 3
    assert abs(v.x) <= 1 << 8 and abs(v.y + (112 << 16)) <= 112  # the top, on the paddle's inside


@pytest.mark.parametrize('pa', (0, 256, 512, 768, 100, 1023))
def test_the_ball_rests_6_px_inside_the_paddle(fw, pa):
    g = api(fw)
    v = Vortex()
    v.pa = pa
    g.place(r(v))
    x, y = at(pa, R_PAD - 6)
    assert abs(v.x - x) <= 112 * 2 and abs(v.y - y) <= 112 * 2


@pytest.mark.parametrize('level, px_per_s', ((0, 120), (1, 135), (2, 150), (9, 255), (10, 270), (11, 270), (99, 270)))
def test_each_level_is_15_px_a_second_faster_up_to_270(fw, level, px_per_s):
    g = api(fw)
    v = Vortex()
    v.level = level
    for a in (0, 100, 256, 700):
        g.head(r(v), a)
        assert abs(math.hypot(v.vx, v.vy) * 60 / 65536 - px_per_s) < 0.01
        assert abs(off(heading(v), a)) < 0.1


def test_levels_cycle_their_layouts_and_aim_again(fw):
    g = api(fw)
    layouts = [[0xffff] * 3, [0x5555, 0xffff, 0xaaaa], [0x0f0f, 0xf0f0, 0xffff]]
    for level in range(7):
        v = Vortex()
        v.level, v.state, v.pa = level, RUNNING, 512
        g.level(r(v))
        assert list(v.brick) == layouts[level % 3] and v.state == AIM
        assert abs(v.x + (112 << 16)) <= 224 and abs(v.y) <= 224


def test_the_paddle_turns_and_wraps_and_the_aimed_ball_follows(fw):
    g = api(fw)
    v = fresh(g, AIM)
    g.vortex_step(r(v), 256)
    assert v.pa == 0 and abs(v.x - (112 << 16)) <= 224 and abs(v.y) <= 224
    g.vortex_step(r(v), -1)
    assert v.pa == 1023
    g.vortex_step(r(v), 2048 + 5)
    assert v.pa == 4


def test_launch_heads_through_the_centre_only_from_aim(fw):
    g = api(fw)
    for pa in range(0, 1024, 37):
        v = fresh(g, AIM)
        g.vortex_step(r(v), pa - 768)
        g.vortex_launch(r(v))
        assert v.state == RUNNING and abs(off(heading(v), pa + 512)) < 1
        assert v.vx * v.x + v.vy * v.y < 0
    v.state = OVER
    before = bytes(v)
    g.vortex_launch(r(v))
    assert bytes(v) == before
    v.state = RUNNING
    before = bytes(v)
    g.vortex_launch(r(v))  # a second press in flight changes nothing
    assert bytes(v) == before


@pytest.mark.parametrize('ring', range(3))
def test_a_brick_breaks_scores_and_reflects_off_its_radius(fw, ring):
    g = api(fw)
    for seg in range(16):
        v = fresh(g)
        v.brick[0], v.brick[1], v.brick[2] = [0xffff if k == ring else 0 for k in range(3)]
        a = seg * 64 + 32
        v.x, v.y = ox, oy = at(a, 40 + 12 * ring - 1)
        vx, vy = at(a + 40, 2)
        v.vx, v.vy = vx, vy
        g.vortex_step(r(v), 0)
        assert v.brick[ring] == 0xffff & ~(1 << seg) and v.score == 10 * (3 - ring)
        assert (v.x, v.y) == (ox, oy)  # back where it was, so it never sits in a ring
        cx, cy = ox + vx, oy + vy
        n = math.hypot(cx, cy)
        dot = (vx * cx + vy * cy) / n
        want = vx - 2 * dot * cx / n, vy - 2 * dot * cy / n
        assert math.hypot(v.vx - want[0], v.vy - want[1]) < 0.003 * math.hypot(vx, vy), (ring, seg)


def test_a_ring_s_edges_are_40_and_76_px_out(fw):
    g = api(fw)
    def enters(x):
        v = fresh(g)
        v.x, v.y, v.vx, v.vy = x - 1, 0, 1, 0
        g.vortex_step(r(v), 0)
        return v.score
    assert enters(40 << 16) == 30  # the inner ring's first subpixel
    assert enters((40 << 16) - 256) == 0
    assert enters(76 << 16) == 0  # past the last ring
    assert enters((76 << 16) - 256) == 10
    assert enters(52 << 16) == 20 and enters((52 << 16) - 256) == 30  # a ring's own edge
    assert enters(64 << 16) == 10 and enters((64 << 16) - 256) == 20


def test_a_gap_lets_the_ball_through(fw):
    g = api(fw)
    v = fresh(g)
    v.brick[0] = 0xffff & ~1
    v.x, v.y, v.vx, v.vy = (40 << 16) - 1, 1 << 10, 1 << 8, 0  # in segment 0
    g.vortex_step(r(v), 0)
    assert v.score == 0 and v.brick[0] == 0xfffe and v.x == (40 << 16) + 255


def test_segments_turn_every_64_clockwise(fw):
    g = api(fw)
    for a, seg in ((0, 0), (64, 1), (128, 2), (256, 4), (512, 8), (768, 12), (1000, 15)):
        v = fresh(g)
        v.x, v.y = at(a + 0.4, 46)
        v.vx, v.vy = 0, 0
        g.vortex_step(r(v), 0)
        assert v.brick[0] == 0xffff & ~(1 << seg), a


def test_the_last_brick_is_the_next_level(fw):
    g = api(fw)
    v = fresh(g)
    v.brick[0], v.brick[1], v.brick[2] = 0, 0, 1 << 3
    v.score = 500
    v.pa = 10
    v.x, v.y = at(3 * 64 + 32, 69)
    g.vortex_step(r(v), 0)
    assert (v.score, v.level, v.state) == (500 + 10 + 100, 1, AIM)
    assert list(v.brick) == [0x5555, 0xffff, 0xaaaa]
    assert abs(math.hypot(v.x, v.y) - (112 << 16)) < 1000 and v.lives == 3


def test_the_paddle_sends_it_back_turned_by_how_far_off_centre_it_hit(fw):
    g = api(fw)
    for d in (-64, -63, -32, -1, 0, 1, 32, 63, 64):
        v = fresh(g)
        v.x, v.y = at(300, 113.9)
        v.vx, v.vy = at(300, 1)
        X, Y = v.x + v.vx >> 8, v.y + v.vy >> 8
        assert g.radius(v.x, v.y) < EDGE <= g.radius(v.x + v.vx, v.y + v.vy)
        a = g.vangle(Y, X)
        v.pa = (a - d) & 1023
        g.vortex_step(r(v), 0)
        assert v.state == RUNNING and v.lives == 3, d
        assert abs(off(heading(v), a + 512 + 2 * d)) < 1, d  # up to 45 degrees at an end
        assert abs(math.hypot(v.vx, v.vy) - V0) < 4
        back = -(v.vx * math.cos(a * math.tau / 1024) + v.vy * math.sin(a * math.tau / 1024))
        assert back / V0 > math.cos(math.tau / 8) - .01  # always in, never along the paddle


def test_past_the_paddle_s_ends_it_goes_through_and_a_life_goes(fw):
    g = api(fw)
    for d in (-65, 65, 512):
        v = fresh(g)
        v.x, v.y = at(300, 113.9)
        v.vx, v.vy = at(300, 1)
        vx, vy = v.vx, v.vy
        v.pa = (g.vangle(v.y + vy >> 8, v.x + vx >> 8) - d) & 1023
        g.vortex_step(r(v), 0)
        assert (v.vx, v.vy, v.lives, v.state) == (vx, vy, 3, RUNNING), d
        while v.state == RUNNING:
            g.vortex_step(r(v), 0)
        assert v.lives == 2 and v.state == AIM and abs(math.hypot(v.x, v.y) - (112 << 16)) < 1000


def test_a_ball_that_was_already_outside_the_paddle_does_not_bounce_back(fw):
    g = api(fw)
    v = fresh(g)
    v.pa, v.y = 0, 0
    v.x, v.vx = (R_PAD - 2) << 16, 1 << 16
    g.vortex_step(r(v), 0)
    assert v.vx == 1 << 16 and v.x == (R_PAD - 1) << 16


def test_the_ball_is_lost_exactly_at_128_px(fw):
    g = api(fw)
    v = fresh(g)
    v.pa, v.y = 512, 0  # away from the ball
    v.x, v.vx = (MISS << 16) - (1 << 8) - 1, 1
    g.vortex_step(r(v), 0)
    assert v.x == (MISS << 16) - (1 << 8) and v.state == RUNNING  # 127.99 px
    v.vx = 1 << 8
    g.vortex_step(r(v), 0)
    assert v.state == AIM and v.lives == 2


def test_the_last_life_ends_the_game_and_over_is_frozen(fw):
    g = api(fw)
    v = fresh(g)
    v.lives, v.pa, v.y = 1, 512, 0
    v.x, v.vx = (MISS << 16) - 1, 2
    g.vortex_step(r(v), 3)
    assert (v.state, v.lives) == (OVER, 0)
    x, y, pa = v.x, v.y, v.pa
    g.vortex_step(r(v), 5)
    assert (v.x, v.y, v.state) == (x, y, OVER) and v.pa == pa + 5  # the paddle alone still turns


def test_a_bounce_inside_the_rings_never_lands_past_a_ring(fw):
    g = api(fw)
    v = fresh(g)
    rng = random.Random(5)
    v.level = 99
    g.head(r(v), 3)
    for _ in range(3000):
        if v.state != RUNNING:
            v.state = RUNNING
            v.level = 99
            g.head(r(v), rng.randrange(1024))
        g.vortex_step(r(v), rng.randrange(-9, 10))
        assert g.radius(v.x, v.y) < (MISS + 5) * 256 and 0 <= v.pa < 1024
        assert all(0 <= b <= 0xffff for b in v.brick)


# Solitaire

def pile(s, p):
    return list(s.card[p][:s.len[p]])


def put(s, p, cards):
    s.len[p] = len(cards)
    for i, c in enumerate(cards):
        s.card[p][i] = c


def suit(c):
    return c // 13


def rank(c):
    return c % 13


def black(c):
    return suit(c) in (0, 3)


def fits_foundation(c, top):
    return rank(c) == 0 if top is None else suit(c) == suit(top) and rank(c) == rank(top) + 1


def fits_column(c, top):
    return rank(c) == 12 if top is None else rank(c) == rank(top) - 1 and black(c) != black(top)


def test_sol_fits_is_klondike_for_every_pair_of_cards(fw):
    g = api(fw)
    s = Sol()
    for top in [None, *range(52)]:
        put(s, 2, [] if top is None else [top | UP])
        put(s, 6, [] if top is None else [top | UP])
        for c in range(52):
            assert bool(g.sol_fits(r(s), c, 2)) == fits_foundation(c, top), (c, top)
            assert bool(g.sol_fits(r(s), c, 6)) == fits_column(c, top), (c, top)


@pytest.mark.parametrize('to', (2, 3, 4, 5))
def test_every_foundation_takes_an_ace(fw, to):
    g = api(fw)
    s = Sol()
    for ace in (0, 13, 26, 39):
        assert g.sol_fits(r(s), ace, to)


def test_a_new_deal(fw):
    g = api(fw)
    for n in range(1, 11):
        seed(fw, n * 2654435761 % 2**32)
        s = Sol()
        g.sol_new(r(s))
        assert [s.len[p] for p in range(PILES)] == [24, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7]
        assert sorted(c & ~UP for p in range(PILES) for c in pile(s, p)) == list(range(52))
        assert not any(c & UP for c in pile(s, STOCK))
        for p in range(TABLEAU, PILES):
            c = pile(s, p)
            assert c[-1] & UP and not any(x & UP for x in c[:-1])


def test_draw_turns_the_stock_s_top_and_a_redeal_keeps_the_order(fw):
    g = api(fw)
    s = Sol()
    put(s, STOCK, [1, 2, 3])
    assert g.sol_draw(r(s)) and pile(s, WASTE) == [3 | UP] and pile(s, STOCK) == [1, 2]
    assert g.sol_draw(r(s)) and g.sol_draw(r(s)) and pile(s, WASTE) == [3 | UP, 2 | UP, 1 | UP] and not s.len[STOCK]
    for _ in range(3):  # unlimited passes
        assert g.sol_draw(r(s)) and pile(s, STOCK) == [1, 2, 3] and not s.len[WASTE]
        assert g.sol_draw(r(s)) and g.sol_draw(r(s)) and g.sol_draw(r(s))
        assert pile(s, WASTE) == [3 | UP, 2 | UP, 1 | UP]


def test_draw_with_nothing_left_draws_nothing(fw):
    g = api(fw)
    s = Sol()
    assert not g.sol_draw(r(s))


def test_draw_holds_a_full_stock(fw):
    g = api(fw)
    s = Sol()
    put(s, STOCK, list(range(24)))
    for k in range(24):
        assert g.sol_draw(r(s))
    assert s.len[WASTE] == 24 and not s.len[STOCK]
    assert g.sol_draw(r(s)) and pile(s, STOCK) == list(range(24))


def test_sol_move_refuses_what_klondike_does(fw):
    g = api(fw)
    s = Sol()
    put(s, STOCK, [5])
    put(s, WASTE, [13 | UP])
    put(s, 6, [13 + 5 | UP])
    put(s, 2, [])
    before = bytes(s)
    assert not g.sol_move(r(s), STOCK, 2)  # the stock is drawn, not moved from
    assert not g.sol_move(r(s), 6, STOCK) and not g.sol_move(r(s), 6, WASTE)  # nothing goes back
    assert not g.sol_move(r(s), 6, 6)
    assert not g.sol_move(r(s), 7, 6)  # an empty column has nothing to move
    assert bytes(s) == before


def test_a_face_down_card_never_moves(fw):
    g = api(fw)
    s = Sol()
    put(s, 6, [12])  # a King, face down
    assert not g.sol_move(r(s), 6, 7) and s.len[6] == 1


def test_the_waste_and_a_foundation_give_only_their_top_card(fw):
    g = api(fw)
    s = Sol()
    put(s, WASTE, [13 + 6 | UP, 0 + 5 | UP])  # 7 of hearts under 6 of spades
    put(s, 6, [0 + 7 | UP])  # 8 of spades: takes the 7, which is not the waste's top
    assert not g.sol_move(r(s), WASTE, 6)
    put(s, 2, [0 | UP, 1 | UP])
    put(s, 7, [13 + 2 | UP])  # 3 of hearts takes the 2 of spades
    assert g.sol_move(r(s), 2, 7) and pile(s, 2) == [0 | UP] and pile(s, 7) == [15 | UP, 1 | UP]
    put(s, 8, [13 + 1 | UP])  # a run is a column's only: the Ace left can go, one card
    assert g.sol_move(r(s), 2, 8) and not s.len[2]


def test_a_foundation_takes_one_card_of_a_run(fw):
    g = api(fw)
    s = Sol()
    put(s, 6, [13 | UP, 1 | UP])  # the Ace of hearts under the 2 of spades, face up
    before = bytes(s)
    assert not g.sol_move(r(s), 6, 2) and bytes(s) == before  # the Ace fits, but it isn't the top card


def test_a_column_takes_the_part_of_a_run_that_fits_and_turns_what_it_uncovers(fw):
    g = api(fw)
    s = Sol()
    put(s, 6, [39 + 2, 0 + 9 | UP, 13 + 8 | UP, 39 + 7 | UP])  # hidden, 10S 9H 8C
    put(s, 7, [26 + 8 | UP])  # the 9 of diamonds takes the 8 of clubs alone
    assert g.sol_move(r(s), 6, 7)
    assert pile(s, 7) == [34 | UP, 46 | UP] and pile(s, 6) == [41, 9 | UP, 21 | UP]
    put(s, 8, [26 + 10 | UP])  # the Jack of diamonds takes the 10 and the 9
    assert g.sol_move(r(s), 6, 8)
    assert pile(s, 8) == [36 | UP, 9 | UP, 21 | UP]
    assert pile(s, 6) == [41 | UP]  # the card underneath turned


def test_only_a_king_starts_an_empty_column_with_what_lies_on_it(fw):
    g = api(fw)
    s = Sol()
    put(s, 6, [3, 12 | UP, 13 + 11 | UP])  # hidden, K spades, Q hearts
    assert g.sol_move(r(s), 6, 7)
    assert pile(s, 7) == [12 | UP, 24 | UP] and pile(s, 6) == [3 | UP]
    put(s, 6, [0 + 11 | UP])
    assert not g.sol_move(r(s), 6, 8) and s.len[8] == 0  # a Queen doesn't


def test_a_whole_column_moving_leaves_it_empty_and_turns_nothing(fw):
    g = api(fw)
    s = Sol()
    put(s, 6, [12 | UP])
    assert g.sol_move(r(s), 6, 7) and s.len[6] == 0 and pile(s, 7) == [12 | UP]


def test_the_longest_run_fits_a_pile(fw):
    g = api(fw)
    s = Sol()
    run = [(0 if k % 2 else 13) + 12 - k | UP for k in range(13)]
    put(s, 6, [39 + k for k in range(6)] + run)
    assert s.len[6] == 19  # 6 down and 13 up: the deepest a column gets
    assert g.sol_move(r(s), 6, 7) and pile(s, 7) == run and s.len[6] == 6 and s.card[6][5] & UP


def oracle_move(s, a, b):
    """Klondike's rules, written from the comment: the new (pile, cards) dict, or None if it can't move."""
    if a == STOCK or a == b or b < FOUND:
        return None
    cards = pile(s, a)
    run = 0
    while run < len(cards) and cards[-1 - run] & UP and (run < 1 or a >= TABLEAU):
        run += 1
    dest = pile(s, b)
    top = dest[-1] & ~UP if dest else None
    for k in range(1, run + 1):
        if k > 1 and b < TABLEAU:
            break
        c = cards[-k] & ~UP
        if fits_foundation(c, top) if b < TABLEAU else fits_column(c, top):
            rest = cards[:-k]
            if rest:
                rest[-1] |= UP
            return {a: rest, b: dest + cards[-k:]}
    return None


def test_sol_move_matches_the_rules_through_random_play(fw):
    g, rng = api(fw), random.Random(6)
    for n in range(1, 7):
        seed(fw, n * 2654435761 % 2**32)
        s = Sol()
        g.sol_new(r(s))
        for _ in range(40):
            legal = []
            for a in range(PILES):
                for b in range(PILES):
                    t = Sol.from_buffer_copy(s)
                    want = oracle_move(s, a, b)
                    assert bool(g.sol_move(r(t), a, b)) == (want is not None), (a, b)
                    if want:
                        legal.append((a, b))
                        for p in range(PILES):
                            assert pile(t, p) == want.get(p, pile(s, p)), (a, b, p)
            if legal and rng.random() < .7:
                g.sol_move(r(s), *rng.choice(legal))
            else:
                g.sol_draw(r(s))
            assert sum(s.len[:PILES]) == 52 and all(n <= 24 for n in s.len[:PILES])


def test_sol_home_uses_the_foundation_of_its_suit_and_the_first_free_one_for_an_ace(fw):
    g = api(fw)
    s = Sol()
    put(s, 2, [0 | UP])
    put(s, 3, [13 | UP])
    put(s, 6, [14 | UP])
    assert g.sol_home(r(s), 6) and pile(s, 3) == [13 | UP, 14 | UP]
    put(s, WASTE, [26 | UP])
    assert g.sol_home(r(s), WASTE) and pile(s, 4) == [26 | UP]
    put(s, 6, [5 | UP])
    assert not g.sol_home(r(s), 6) and s.len[6] == 1


def test_sol_won_is_all_52_home(fw):
    g = api(fw)
    s = Sol()
    for p in (2, 3, 4, 5):
        put(s, p, [(p - 2) * 13 + k | UP for k in range(13)])
    assert g.sol_won(r(s))
    s.len[5] = 12
    assert not g.sol_won(r(s))
    put(s, 6, [51 | UP])  # the King in a column is not home
    assert not g.sol_won(r(s))


def endgame(rng):
    """A position every card up and dealt: a won game taken apart by legal moves backwards."""
    s = Sol()
    for p in (2, 3, 4, 5):
        put(s, p, [(p - 2) * 13 + k | UP for k in range(13)])
    for _ in range(rng.randrange(0, 200)):
        f = rng.randrange(2, 6)
        if not s.len[f]:
            continue
        c = s.card[f][s.len[f] - 1] & ~UP
        cols = [p for p in range(6, 13) if fits_column(c, s.card[p][s.len[p] - 1] & ~UP if s.len[p] else None)]
        if cols:
            p = rng.choice(cols)
            s.card[p][s.len[p]] = c | UP
            s.len[p] += 1
            s.len[f] -= 1
    return s


def test_sol_finish_brings_any_finished_position_home(fw):
    g, rng = api(fw), random.Random(7)
    spread = 0
    for _ in range(300):
        s = endgame(rng)
        spread = max(spread, sum(s.len[TABLEAU:PILES]))
        g.sol_finish(r(s))
        assert g.sol_won(r(s))
        for p in (2, 3, 4, 5):
            f = [c & ~UP for c in pile(s, p)]
            assert f == list(range(f[0], f[0] + 13)) and rank(f[0]) == 0
    assert spread > 20


def test_sol_finish_waits_for_the_stock_the_waste_and_a_face_down_card(fw):
    g, rng = api(fw), random.Random(8)
    s = endgame(rng)
    while not s.len[6]:
        s = endgame(rng)
    want = bytes(s)
    put(s, WASTE, [])
    t = Sol.from_buffer_copy(s)
    t.card[6][0] &= ~UP
    want = bytes(t)
    g.sol_finish(r(t))
    assert bytes(t) == want
    t = Sol.from_buffer_copy(s)
    t.len[STOCK] = 1
    want = bytes(t)
    g.sol_finish(r(t))
    assert bytes(t) == want
    t = Sol.from_buffer_copy(s)
    t.len[WASTE] = 1
    want = bytes(t)
    g.sol_finish(r(t))
    assert bytes(t) == want


# Snake

def body(s):
    return [s.body[(s.tail + i) % 400] for i in range(s.len)]


def snake(g, cells=None, d=1, food=399):
    s = Snake()
    g.snake_new(r(s))
    if cells:
        s.len = len(cells)
        for i, c in enumerate(cells):
            s.body[i] = c
    s.dir, s.food = d, food
    return s


def test_snake_turns_are_relative_and_wrap(fw):
    g = api(fw)
    for d, turn, want in ((0, -1, 3), (3, 1, 0), (1, 1, 2), (2, -1, 1)):
        s = snake(g, d=d)
        g.snake_turn(r(s), turn)
        assert s.dir == want and s.turned == 1


def test_snake_keeps_one_turn_waiting_and_drops_the_rest(fw):
    g = api(fw)
    s = snake(g)
    g.snake_turn(r(s), 1)
    g.snake_turn(r(s), -1)
    g.snake_turn(r(s), 1)
    assert (s.dir, s.next) == (2, -1)
    assert g.snake_step(r(s)) == 0
    assert (s.dir, s.next, s.turned) == (1, 0, 1)  # spent, and it counts as this step's turn
    g.snake_turn(r(s), 1)
    assert (s.dir, s.next) == (1, 1)


def test_a_dead_snake_takes_no_turns(fw):
    g = api(fw)
    s = snake(g)
    s.state = OVER
    g.snake_turn(r(s), 1)
    assert (s.dir, s.turned, s.next) == (1, 0, 0)


@pytest.mark.parametrize('score, ms', ((0, 160), (1, 156), (22, 72), (23, 70), (24, 70), (1000, 70)))
def test_snake_speeds_up_to_70_ms(fw, score, ms):
    g = api(fw)
    s = Snake()
    s.score = score
    assert g.snake_ms(r(s)) == ms


def test_snake_the_body_wraps_round_its_ring(fw):
    g = api(fw)
    s = Snake()
    g.snake_new(r(s))
    s.tail, s.len, s.dir, s.food = 398, 3, 1, 399
    s.body[398], s.body[399], s.body[0] = 100, 101, 102
    assert g.snake_step(r(s)) == 0
    assert body(s) == [101, 102, 103] and s.tail == 399
    assert s.body[1] == 103
    assert g.snake_step(r(s)) == 0
    assert body(s) == [102, 103, 104] and s.tail == 0


def test_snake_eating_the_last_free_cell_fills_the_board(fw):
    g = api(fw)
    cells = list(range(399, 0, -1))  # 399 cells, the head at 1; cell 0 the last free
    s = snake(g, cells, d=3, food=0)
    s.len = 399
    assert g.snake_step(r(s)) == 1
    assert s.len == 400 and s.state == OVER and s.score == 1
    assert sorted(body(s)) == list(range(400))
    assert g.snake_step(r(s)) == -1


def test_snake_on_the_last_rows_and_columns(fw):
    g = api(fw)
    for head, d, dies in ((19, 1, True), (19, 0, True), (0, 3, True), (0, 0, True), (399, 2, True), (399, 1, True),
                          (380, 2, True), (380, 3, True), (20, 3, True), (39, 1, True), (19, 2, False), (380, 0, False)):
        s = snake(g, [head], d=d, food=200)
        assert (g.snake_step(r(s)) == -1) == dies, (head, d)
        assert (s.state == OVER) == dies


def oracle_step(s):
    x, y = body(s)[-1] % 20, body(s)[-1] // 20
    x += {1: 1, 3: -1}.get(s.dir, 0)
    y += {2: 1, 0: -1}.get(s.dir, 0)
    if not (0 <= x < 20 and 0 <= y < 20):
        return -1, None
    cell = y * 20 + x
    ate = cell == s.food
    if cell in (body(s) if ate else body(s)[1:]):
        return -1, None
    return int(ate), (body(s) if ate else body(s)[1:]) + [cell]


def test_snake_steps_match_the_rules_through_random_play(fw):
    g, rng = api(fw), random.Random(9)
    deaths = 0
    for n in range(1, 31):
        seed(fw, n)
        s = Snake()
        g.snake_new(r(s))
        for _ in range(800):
            g.snake_turn(r(s), rng.choice((-1, 0, 0, 0, 1)))
            want, cells = oracle_step(s)
            assert g.snake_step(r(s)) == want
            if want < 0:
                deaths += 1
                assert s.state == OVER
                break
            assert body(s) == cells and s.food not in cells and len(set(cells)) == len(cells)
    assert deaths > 20


def test_food_lands_only_on_free_cells_and_reaches_them_all(fw):
    g = api(fw)
    seed(fw, 7)
    s = snake(g, [207, 208, 209])
    seen = set()
    for _ in range(4000):
        g.sn_food(r(s))
        seen.add(s.food)
    assert not seen & {207, 208, 209} and len(seen) == 397


# The page: its save files, keys, timers. page_open and the list go to stubs, so a page is a number.

class Card:
    """blob_io's files: fopen, fread, fwrite and rename on a dict."""
    def __init__(self, fw, files=None):
        self.files, self.open, self.fail = dict(files or {}), {}, False
        for name in 'fopen fread fwrite rename'.split():
            fw.mock('fw_' + name, getattr(self, name))

    def fopen(self, path, mode, *_):
        name, write = C.string_at(path).decode(), C.string_at(mode)[:1] == b'w'
        if not write and name not in self.files:
            return 0
        handle = 0x1000 + len(self.open)
        self.open[handle] = [name, 0]
        if write:
            self.files[name] = b''
        return handle

    def fread(self, buf, size, n, f, *_):
        name, at = self.open[f]
        data = self.files[name][at:at + size * n]
        C.memmove(buf, data, len(data))
        self.open[f][1] += len(data)
        return len(data) // size

    def fwrite(self, buf, size, n, f, *_):
        if self.fail:
            return 0
        self.files[self.open[f][0]] += C.string_at(buf, size * n)
        return n

    def rename(self, a, b, *_):
        self.files[C.string_at(b).decode()] = self.files.pop(C.string_at(a).decode())
        return 0


PAGE, LATCH = 0x7000, 0xa37c8b


class Page:
    """The Games page open on stubs: a clock, a timer list and the card."""
    def __init__(self, fw, files=None):
        self.fw, self.g, self.card = fw, api(fw), Card(fw, files)
        self.now, self.timers, self.toasts = 1000, [], []
        fw.mock('window_create', lambda *_: PAGE)
        fw.mock('widget_get_prop_int', lambda w, k, d, *_: 290 if C.string_at(k) == b'h' else d)
        fw.mock('timer_add', self.timer)
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('window_manager_get_top_window', lambda *_: PAGE)
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        self.view = C.create_string_buffer(0x400)  # ringnav_select reads the list's fields
        fw.mock('scroll_view_create', lambda *_: C.addressof(self.view))
        fw.mock('list_item_create', lambda *_: 0x7100)
        fw.mock('widget_get_child', lambda *_: 0x7200)
        fw.mock('fw_fflush', lambda *_: 0)
        fw.mock('fw_fsync', lambda *_: 0)
        fw.mock('fw_fclose', lambda *_: 0)
        no_held_return()
        self.g.games_open(None, None)

    def timer(self, fn, ctx, ms, *_):
        self.timers.append((fn, ms))
        return len(self.timers)

    @property
    def s(self):
        return Game.from_address(self.fw.var('games__g', C.c_void_p).value)

    def key(self, *keys):
        for k in keys:
            self.last = self.g.games_key(PAGE, k)

    def go(self, screen):
        self.s.target = screen
        self.g.show(None)

    def close(self):
        self.g.closed(None, None)


SUDOKU_FILE, VORTEX_FILE, SOLITAIRE_FILE, SNAKE_FILE = (f'/mnt/data/ringnav-{n}' for n in 'sudoku vortex solitaire snake'.split())


def sealed(fw, struct, magic):
    api(fw).games_seal(r(struct), C.sizeof(struct), magic)
    return bytes(struct)


def no_held_return():
    """games_key reads Return's stock latch at its device address: a zero page there unless this python's own
    image is (its non-PIE build is), which a read survives but only a zero there leaves the key to the game."""
    libc.mmap.restype = C.c_void_p
    libc.mmap.argtypes = [C.c_void_p, C.c_size_t, C.c_int, C.c_int, C.c_int, C.c_long]
    libc.mmap(LATCH & ~0xfff, 4096, 3, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS | 0x100000, -1, 0)  # MAP_FIXED_NOREPLACE
    if C.c_ubyte.from_address(LATCH).value:
        pytest.skip('the latch address is taken in this process')


def test_games_live_is_a_frame_timer_running(fw):
    g = api(fw)
    assert not g.games_live()  # no page
    p = Page(fw)
    assert not g.games_live()
    p.go(VORTEX)
    assert g.games_live()
    p.close()
    assert not g.games_live()


def test_opening_twice_keeps_the_first_and_a_failed_page_leaves_none(fw):
    g = api(fw)
    p = Page(fw)
    first = fw.var('games__g', C.c_void_p).value
    g.games_open(None, None)
    assert fw.var('games__g', C.c_void_p).value == first
    p.close()
    fw.mock('window_create', lambda *_: 0)
    g.games_open(None, None)
    assert fw.var('games__g', C.c_void_p).value is None


def test_a_missing_save_is_a_fresh_game(fw):
    p = Page(fw)
    s = p.s
    assert s.sud.magic == 0 and s.sud.cur == 0 and sum(1 for c in s.sud.cell if c) >= 17
    assert sum(s.sol.len[:PILES]) == 52 and s.sol.len[STOCK] == 24
    assert s.best.score == 0 and s.sn_best.score == 0


def test_a_sudoku_save_comes_back_with_its_cursor(fw):
    d = Sudoku()
    d.cell[0], d.cell[80], d.cur = 16 | 3, 7, 80
    p = Page(fw, {SUDOKU_FILE: sealed(fw, d, SUD)})
    assert (p.s.sud.cell[0], p.s.sud.cell[80], p.s.sud.cur) == (19, 7, 80)


def test_a_sudoku_save_with_its_cursor_off_the_grid_is_a_fresh_game(fw):
    d = Sudoku()
    d.cell[80], d.cur = 7, 81
    p = Page(fw, {SUDOKU_FILE: sealed(fw, d, SUD)})
    assert p.s.sud.cur == 0 and p.s.sud.magic == 0


def full_table(g):
    s = Sol()
    g.sol_new(r(s))
    return s


def test_a_solitaire_save_comes_back(fw):
    d = full_table(api(fw))
    d.cur = 12
    p = Page(fw, {SOLITAIRE_FILE: sealed(fw, d, SOL)})
    assert bytes(p.s.sol) == bytes(d)


@pytest.mark.parametrize('field, value', (('cur', 13), ('len', 25)))
def test_a_solitaire_save_past_its_limits_is_a_fresh_game(fw, field, value):
    d = full_table(api(fw))
    if field == 'cur':
        d.cur = value
    else:
        d.len[12] = value
    p = Page(fw, {SOLITAIRE_FILE: sealed(fw, d, SOL)})
    assert p.s.sol.magic == 0 and p.s.sol.cur == 0 and sum(p.s.sol.len[:PILES]) == 52


def test_a_solitaire_save_at_its_limits_stays(fw):
    d = full_table(api(fw))
    d.len[12] = 24
    d.cur = 12
    p = Page(fw, {SOLITAIRE_FILE: sealed(fw, d, SOL)})
    assert p.s.sol.magic == SOL and p.s.sol.cur == 12 and p.s.sol.len[12] == 24


@pytest.mark.parametrize('name, magic, attr', ((VORTEX_FILE, BEST, 'best'), (SNAKE_FILE, SNK, 'sn_best')))
def test_a_best_score_comes_back_and_a_bad_one_is_zero(fw, name, magic, attr):
    d = Best(score=4321)
    good = sealed(fw, d, magic)
    p = Page(fw, {name: good})
    assert getattr(p.s, attr).score == 4321
    p.close()
    bad = Page(fw, {name: good[:-1] + bytes([good[-1] ^ 1])})
    assert getattr(bad.s, attr).score == 0
    bad.close()
    other = Page(fw, {name: good[:8]})  # short
    assert getattr(other.s, attr).score == 0


def test_each_game_s_save_carries_its_own_magic(fw):
    d = Best(score=99)
    p = Page(fw, {VORTEX_FILE: sealed(fw, d, SNK)})
    assert p.s.best.score == 0


def test_saves_hostile_sweep(fw):
    rng = random.Random(10)
    p = Page(fw)
    magics = {SUDOKU_FILE: (SUD, 92), VORTEX_FILE: (BEST, 12), SNAKE_FILE: (SNK, 12), SOLITAIRE_FILE: (SOL, 336)}
    g = api(fw)
    valid = full_table(g)
    p.close()
    for k in range(2000):
        files = {}
        for name, (magic, size) in magics.items():
            kind = rng.randrange(6)
            if kind == 0:
                continue
            data = bytearray(rng.randbytes(size))
            if kind == 1:
                data = data[:rng.randrange(size)]  # short: blob_io's zeroes
            elif kind == 3:
                data[:4] = magic.to_bytes(4, 'little')  # the magic alone
            elif kind == 4:  # a sealed save, one byte then hurt
                if name == SOLITAIRE_FILE:
                    data = bytearray(valid)
                data[:4] = magic.to_bytes(4, 'little')
                data[-4:] = fnv1a(data[:-4]).to_bytes(4, 'little')
                data[rng.randrange(size)] ^= 1 << rng.randrange(8)
            files[name] = bytes(data)
        p.card.files = files
        g.games_open(None, None)
        s = p.s
        assert s.sud.cur <= 80 and s.sol.cur < PILES and all(n <= 24 for n in s.sol.len[:PILES])
        assert s.sud.magic in (0, SUD) and s.sol.magic in (0, SOL)
        if s.sol.magic == 0:
            assert sum(s.sol.len[:PILES]) == 52
        if k % 8 == 0:
            p.go(rng.choice((SUDOKU, SOLITAIRE)))
            p.key(*rng.choices((CENTER, NEXT, PREV, PLAY, FWD_BTN, BACK_BTN), k=12))
        p.close()


def test_the_chooser_and_other_windows_keep_their_keys(fw):
    p = Page(fw)
    p.key(CENTER)
    assert p.last == 0
    p.go(SUDOKU)
    assert p.g.games_key(PAGE + 1, CENTER) == 0
    p.key(CENTER)
    assert p.last == 1


def test_sudoku_the_cursor_walks_and_wraps(fw):
    p = Page(fw)
    p.go(SUDOKU)
    p.key(PREV)
    assert p.s.sud.cur == 80
    p.key(NEXT)
    assert p.s.sud.cur == 0
    p.key(BACK_BTN)
    assert p.s.sud.cur == 72
    p.key(FWD_BTN)
    assert p.s.sud.cur == 0
    p.key(FWD_BTN)
    assert p.s.sud.cur == 9
    assert p.s.dirty & 1  # a moved cursor is kept on leaving


def blank(s):
    return next(i for i in range(81) if not s.sud.cell[i])


def test_sudoku_centre_opens_the_digit_and_the_wheel_picks_blank_or_1_to_9(fw):
    p = Page(fw)
    p.go(SUDOKU)
    s = p.s
    i = blank(s)
    s.sud.cur = i
    p.key(CENTER)
    assert s.picking and s.pick == 0
    p.key(PREV)
    assert s.pick == 9  # wraps through blank
    p.key(NEXT)
    assert s.pick == 0
    p.key(NEXT, NEXT, NEXT)
    assert s.pick == 3
    p.key(CENTER)
    assert s.sud.cell[i] == 3 and not s.picking
    p.key(CENTER)
    assert s.picking and s.pick == 3  # an entered cell opens at its digit
    p.key(NEXT, RETURN)
    assert s.sud.cell[i] == 3 and not s.picking  # Return cancels


def test_sudoku_centre_on_a_clue_does_nothing(fw):
    p = Page(fw)
    p.go(SUDOKU)
    s = p.s
    s.sud.cur = next(i for i in range(81) if s.sud.cell[i] & 16)
    p.key(CENTER)
    assert not s.picking


def test_sudoku_a_finished_grid_takes_no_digit_and_play_starts_anew(fw):
    p = Page(fw)
    p.go(SUDOKU)
    s = p.s
    s.sud.cell[:] = SOLVED
    s.sud.cur = 5
    p.key(CENTER)
    assert not s.picking
    p.key(PLAY)  # no asking over a solved one
    assert not s.confirm and not p.g.sud_done(r(s.sud))


def test_sudoku_play_asks_first_over_an_unfinished_game(fw):
    p = Page(fw)
    p.go(SUDOKU)
    s = p.s
    i = blank(s)
    s.sud.cell[i] = 1
    p.key(PLAY)
    assert s.confirm and s.sud.cell[i] == 1
    p.key(RETURN)
    assert not s.confirm and s.sud.cell[i] == 1
    p.key(PLAY, CENTER)
    assert not s.confirm and not p.g.sud_started(r(s.sud))


def test_a_move_arms_one_save_timer_and_it_writes_a_sealed_file(fw):
    p = Page(fw)
    p.go(SUDOKU)
    s = p.s
    s.sud.cur = blank(s)
    p.key(CENTER, NEXT, CENTER)
    timers = len(p.timers)
    assert p.timers[-1][1] == 2000 and s.save
    s.sud.cur = blank(s)
    p.key(CENTER, NEXT, CENTER)
    assert len(p.timers) == timers  # one timer, from the first unsaved change
    assert s.dirty & 1
    p.g.save_due(None)
    assert not s.save and not s.dirty
    raw = p.card.files[SUDOKU_FILE]
    assert len(raw) == 92 and raw[:4] == SUD.to_bytes(4, 'little')
    assert raw[-4:] == fnv1a(raw[:-4]).to_bytes(4, 'little')


def test_saved_games_read_back_as_they_were_left(fw):
    p = Page(fw)
    p.go(SUDOKU)
    s = p.s
    s.sud.cur = blank(s)
    i = s.sud.cur
    p.key(CENTER, NEXT, NEXT, CENTER)
    grid = bytes(s.sud.cell)
    p.close()
    again = Page(fw, p.card.files)
    assert bytes(again.s.sud.cell) == grid and again.s.sud.cur == i and again.s.sud.cell[i] == 2


def heard(fw):
    toasts = []
    fw.mock('navigator_to_with_context', lambda page, info, *_: toasts.append(C.string_at(info + 8)))  # toast's dialog
    return toasts


def test_closing_writes_a_dirty_game_silently_even_if_the_write_fails(fw):
    p = Page(fw)
    toasts = heard(fw)
    p.go(SUDOKU)
    p.s.dirty = 3
    p.card.fail = True
    p.close()
    assert toasts == []


def test_a_failed_save_says_so_when_it_can(fw):
    p = Page(fw)
    toasts = heard(fw)
    p.s.dirty = 1
    p.card.fail = True
    p.g.flush(1)
    assert toasts == [b"Couldn't save"] and not p.s.dirty


def test_flush_writes_only_the_dirty_games(fw):
    p = Page(fw)
    p.s.dirty = 2
    p.g.flush(0)
    assert SOLITAIRE_FILE in p.card.files and SUDOKU_FILE not in p.card.files
    assert p.card.files[SOLITAIRE_FILE][:4] == SOL.to_bytes(4, 'little')


def test_a_game_over_saves_a_record_only(fw):
    p = Page(fw)
    p.go(VORTEX)
    s = p.s
    s.best.score = 100
    s.vx.score, s.vx.state = 100, OVER
    game_over = fw.fn('games__game_over', None)
    game_over()
    assert not s.record and VORTEX_FILE not in p.card.files
    s.vx.score = 101
    game_over()
    assert s.record and s.best.score == 101
    raw = p.card.files[VORTEX_FILE]
    assert len(raw) == 12 and raw[:4] == BEST.to_bytes(4, 'little') and raw[4:8] == (101).to_bytes(4, 'little')
    assert raw[-4:] == fnv1a(raw[:-4]).to_bytes(4, 'little')


def test_snake_s_record_is_its_own_file(fw):
    p = Page(fw)
    p.go(SNAKE)
    s = p.s
    s.sn.score = 7
    fw.fn('games__game_over', None)()
    assert s.sn_best.score == 7 and SNAKE_FILE in p.card.files and VORTEX_FILE not in p.card.files


def creep(s):
    s.vx.state = RUNNING
    s.vx.x, s.vx.y, s.vx.vx, s.vx.vy = 0, 0, 0, 1 << 8  # so y counts the steps


def test_vortex_runs_60_steps_a_second_at_any_frame_rate(fw):
    p = Page(fw)
    p.go(VORTEX)
    s = p.s
    creep(s)
    for _ in range(100):
        p.now += 33
        p.g.frame(None)
    assert s.vx.y == 198 << 8  # 3.3 s, the fractions of a step carried across frames


def test_a_frame_never_runs_more_than_4_steps_and_drops_the_backlog(fw):
    p = Page(fw)
    p.go(VORTEX)
    s = p.s
    creep(s)
    p.now = s.at + 1000
    assert p.g.frame(None) == 8  # RET_REPEAT
    assert s.vx.y == 4 << 8 and s.acc == 0
    p.now += 50  # 3 steps' time
    p.g.frame(None)
    assert s.vx.y == 7 << 8 and s.acc == 0


def test_the_paddle_loses_no_wheel_travel_across_frames(fw):
    p = Page(fw)
    p.go(VORTEX)
    s = p.s
    travel = fw.var('wheel_travel', C.c_int)
    pa = s.vx.pa
    total = 0
    for k in range(300):
        p.now += 17
        travel.value = 3
        total += 3
        p.g.frame(None)
    assert abs(off(s.vx.pa, pa - total * 1024 / 200)) <= 1  # 1024 for 200 units, the wheel's x falling clockwise


def test_the_screen_off_or_another_window_pauses_a_game(fw):
    p = Page(fw)
    p.go(VORTEX)
    s = p.s
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    assert p.g.frame(None) == 0
    assert s.paused == HELD and not s.frame
    p.key(PLAY)
    assert s.paused == RUN and s.frame
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    fw.mock('window_manager_get_top_window', lambda *_: PAGE + 8)
    assert p.g.frame(None) == 0
    assert s.paused == HELD and not s.frame


def test_vortex_game_over_stops_the_frame_and_saves_the_record(fw):
    p = Page(fw)
    p.go(VORTEX)
    s = p.s
    s.vx.state, s.vx.lives, s.vx.score, s.vx.pa = RUNNING, 1, 30, 512
    s.vx.x, s.vx.vx = (MISS << 16) - 1, 2
    s.vx.y = s.vx.vy = 0
    p.now += 20
    assert p.g.frame(None) == 0
    assert s.vx.state == OVER and not s.frame and s.best.score == 30 and s.record
    p.key(CENTER)  # again
    assert s.vx.state == AIM and s.vx.score == 0 and s.frame


def test_a_broken_brick_is_remembered_for_its_flash(fw):
    p = Page(fw)
    p.go(VORTEX)
    s = p.s
    s.vx.state = RUNNING
    s.vx.x, s.vx.y = at(5 * 64 + 32, 39)
    s.vx.vx, s.vx.vy = at(5 * 64 + 32, 2)
    p.now += 17
    p.g.frame(None)
    assert s.vx.score == 30 and (s.pop_ring, s.pop_seg, s.pop_at) == (0, 5, p.now)


def test_the_last_brick_flashes_though_the_level_refilled(fw):
    p = Page(fw)
    p.go(VORTEX)
    s = p.s
    s.vx.state = RUNNING
    s.vx.brick[0], s.vx.brick[1], s.vx.brick[2] = 0, 0, 1 << 9
    s.vx.x, s.vx.y = at(9 * 64 + 32, 63)
    s.vx.vx, s.vx.vy = at(9 * 64 + 32, 2)
    p.now += 17
    p.g.frame(None)
    assert s.vx.level == 1 and (s.pop_ring, s.pop_seg) == (2, 9)


def test_snake_steps_by_time_two_a_frame_at_most(fw):
    p = Page(fw)
    p.go(SNAKE)
    s = p.s
    s.sn.food = 0
    head = s.sn.body[2]
    p.now += 159
    p.g.frame(None)
    assert s.sn.body[(s.sn.tail + 2) % 400] == head and s.acc == 159
    p.now += 1
    p.g.frame(None)
    assert s.sn.body[(s.sn.tail + 2) % 400] == head + 1 and s.acc == 0
    p.now += 1000
    p.g.frame(None)
    assert s.sn.body[(s.sn.tail + 2) % 400] == head + 3 and s.acc == 0


def test_snake_s_crash_ends_the_frame_with_its_record(fw):
    p = Page(fw)
    p.go(SNAKE)
    s = p.s
    s.sn.body[(s.sn.tail + 2) % 400] = 219
    s.sn.score = 5
    p.now += 200
    assert p.g.frame(None) == 0
    assert s.sn.state == OVER and s.sn_best.score == 5 and not s.frame


def test_return_opens_the_menu_and_the_wheel_picks(fw):
    p = Page(fw)
    p.go(VORTEX)
    s = p.s
    p.key(RETURN)
    assert s.paused == MENU and not s.frame and s.choice == 0
    p.key(NEXT)
    assert s.choice == 1
    p.key(PREV)
    assert s.choice == 0
    p.key(CENTER)
    assert s.paused == RUN and s.frame  # Continue
    p.key(RETURN, NEXT, CENTER)
    assert s.target == CHOOSER and s.timer  # Quit


def test_play_pauses_and_resumes_and_a_paused_centre_resumes(fw):
    p = Page(fw)
    p.go(VORTEX)
    s = p.s
    p.key(PLAY)
    assert s.paused == HELD and not s.frame
    p.key(PLAY)
    assert s.paused == RUN and s.frame
    p.key(PLAY, CENTER)
    assert s.paused == RUN and s.vx.state == AIM  # Centre resumed, it didn't launch
    p.key(CENTER)
    assert s.vx.state == RUNNING


def test_vortex_over_return_is_the_chooser(fw):
    p = Page(fw)
    p.go(VORTEX)
    s = p.s
    s.vx.state = OVER
    p.key(RETURN)
    assert s.target == CHOOSER


def test_snake_the_side_keys_steer_and_the_wheel_is_ignored(fw):
    p = Page(fw)
    p.go(SNAKE)
    s = p.s
    p.key(NEXT, PREV)
    assert s.sn.dir == 1 and not s.sn.turned
    p.key(FWD_BTN)
    assert s.sn.dir == 2
    p.key(BACK_BTN)
    assert s.sn.next == -1
    p.key(RETURN)
    p.key(FWD_BTN)  # the menu: a side key picks
    assert s.choice == 1 and s.sn.dir == 2
    p.key(NEXT)  # so does the wheel
    assert s.choice == 0


def test_snake_after_a_crash_centre_plays_again(fw):
    p = Page(fw)
    p.go(SNAKE)
    s = p.s
    s.sn.state, s.sn.score = OVER, 4
    p.key(CENTER)
    assert s.sn.state == RUNNING and s.sn.score == 0 and s.frame


def test_a_key_paused_snake_takes_no_turn(fw):
    p = Page(fw)
    p.go(SNAKE)
    s = p.s
    p.key(PLAY, FWD_BTN)
    assert s.sn.dir == 1


def test_solitaire_the_side_keys_jump_between_a_pile_and_the_column_under_it(fw):
    p = Page(fw)
    p.go(SOLITAIRE)
    s = p.s
    down = {0: 6, 1: 7, 2: 9, 3: 10, 4: 11, 5: 12}
    up = {6: 0, 7: 1, 8: 1, 9: 2, 10: 3, 11: 4, 12: 5}
    for a, b in down.items():
        s.sol.cur = a
        p.key(FWD_BTN)
        assert s.sol.cur == b, a
    for a, b in up.items():
        s.sol.cur = a
        p.key(BACK_BTN)
        assert s.sol.cur == b, a


def test_solitaire_the_wheel_walks_13_piles_and_wraps(fw):
    p = Page(fw)
    p.go(SOLITAIRE)
    s = p.s
    p.key(PREV)
    assert s.sol.cur == 12
    p.key(NEXT)
    assert s.sol.cur == 0


def game_table(s):
    s.sol.cur = 0
    for k in range(PILES):
        put(s.sol, k, [])


def test_solitaire_centre_draws_picks_and_drops(fw):
    p = Page(fw)
    p.go(SOLITAIRE)
    s = p.s
    game_table(s)
    put(s.sol, STOCK, [13])
    put(s.sol, 6, [26 + 8 | UP])
    p.key(CENTER)  # the stock draws
    assert pile(s.sol, WASTE) == [13 | UP] and not s.sol.len[STOCK]
    s.sol.cur = WASTE
    p.key(CENTER)  # picked up
    assert s.picking and s.pick == WASTE
    s.sol.cur = 6
    p.key(CENTER)  # the Ace of hearts doesn't go on the 9: still held
    assert s.picking and s.sol.len[WASTE] == 1
    s.sol.cur = 2
    p.key(CENTER)
    assert not s.picking and pile(s.sol, 2) == [13 | UP] and s.dirty & 2


def test_solitaire_centre_on_the_same_pile_sends_it_home_or_puts_it_back(fw):
    p = Page(fw)
    p.go(SOLITAIRE)
    s = p.s
    game_table(s)
    put(s.sol, 6, [26 + 8 | UP, 0 | UP])
    s.sol.cur = 6
    p.key(CENTER, CENTER)
    assert pile(s.sol, 2) == [0 | UP] and not s.picking
    p.key(CENTER, CENTER)  # the 9 has nowhere to go: put back
    assert pile(s.sol, 6) == [34 | UP] and not s.picking
    s.sol.cur = 2
    p.key(CENTER, CENTER)  # a foundation's card stays
    assert pile(s.sol, 2) == [0 | UP] and not s.picking


def test_solitaire_return_drops_what_is_held_then_leaves(fw):
    p = Page(fw)
    p.go(SOLITAIRE)
    s = p.s
    s.target = SOLITAIRE
    s.sol.cur = 6
    p.key(CENTER, RETURN)
    assert not s.picking and s.target == SOLITAIRE
    p.key(RETURN)
    assert s.target == CHOOSER


def test_solitaire_play_asks_unless_it_is_won(fw):
    p = Page(fw)
    p.go(SOLITAIRE)
    s = p.s
    deal = pile(s.sol, STOCK)
    p.key(PLAY)
    assert s.confirm
    p.key(RETURN)
    assert not s.confirm and pile(s.sol, STOCK) == deal
    game_table(s)
    for f in (2, 3, 4, 5):
        put(s.sol, f, [(f - 2) * 13 + k | UP for k in range(13)])
    p.key(PLAY)
    assert not s.confirm and sum(s.sol.len[2:6]) == 0  # a new deal


def test_solitaire_the_last_move_finishes_the_game(fw):
    p = Page(fw)
    p.go(SOLITAIRE)
    s = p.s
    game_table(s)
    for f, cards in ((2, range(13)), (3, range(13, 26)), (4, range(26, 39)), (5, range(39, 50))):
        put(s.sol, f, [c | UP for c in cards])
    put(s.sol, 6, [50 | UP])
    put(s.sol, 7, [51 | UP])
    s.sol.cur = 6
    p.key(CENTER, CENTER)  # the Queen of clubs home: sol_finish takes the King
    assert p.g.sol_won(r(s.sol))


def test_leaving_a_game_saves_and_returns_to_its_row(fw):
    p = Page(fw)
    p.go(SOLITAIRE)
    s = p.s
    s.sol.cur = 3
    p.key(NEXT)  # dirty: the cursor
    props = {}
    fw.mock('widget_set_prop_int', lambda w, k, v, *_: props.update({C.string_at(k): v}))
    p.go(CHOOSER)
    assert s.screen == CHOOSER and SOLITAIRE_FILE in p.card.files
    assert props[b'_ringnav_index'] == SOLITAIRE - 1 and props[b'_ringnav_count'] == 4


def test_a_screen_left_with_keyup_in_the_chooser_goes_back(fw):
    p = Page(fw)
    p.g.leave(None)
    assert fw.calls('navigator_back')
    p.go(SUDOKU)
    p.g.leave(None)
    assert p.s.target == CHOOSER
