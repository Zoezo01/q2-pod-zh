"""patch/playback.c: the grouped queue's order, Play Next's list, the wheel's ticks and the queue snapshot."""
import ctypes as C
import random
import re
import struct

import pytest

from conftest import FNV_PRIME, O, Deques, Files, claim, fnv1a, in_fork, libc  # noqa: F401

MAGIC = 0x5132524e
HEADER = '<14I'
QUEUE_FILE = '/mnt/data/ringnav-queue'
CHECKPOINT = QUEUE_FILE + '-elapsed'
FOLDER, LIBRARY = 1, 0xf001
ONE_SONG, ONE_GROUP, ONE_PASS, REPEAT_SONG, REPEAT_GROUP, REPEAT_ALL = range(6)
SEEDS = (1, 7, 0x2545f491, 0xdeadbeef, 12345)


class Entry(C.Structure):
    _fields_ = [(n, C.c_uint) for n in ('index', 'group', 'rank')]


def fields(names, ctype=C.c_int):
    return [(n, ctype) for n in names.split()]


class State(C.Structure):
    _fields_ = (fields('active shuffle repeat folder restoring dirty grouping') +
                fields('n cursor npend hn stamp', C.c_uint) + [('pend', C.c_uint * 64)] +
                fields('saved_elapsed save_failed resume_pending resume_elapsed resume_wait') +
                fields('saved_pos saved_sum saved_queue', C.c_uint) + fields('next_valid next_value') +
                fields('next_pos resume_key resume_pos', C.c_uint) + fields('order cycle', C.POINTER(Entry)) +
                fields('groups seen history', C.POINTER(C.c_uint)) + [('poll_stamp', C.c_uint)])


class Player:
    """A queue and its player as stock keeps them, for the C to run against."""
    def __init__(self, fw, mcl):
        self.fw, self.mcl = fw, mcl
        self.s = fw.var('playback__s', State)
        self.deques = Deques(fw)
        self.keep = []
        self.handle = self.deques.new()
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.handle
        self.set = fw.fn('playback_set', C.c_int, C.c_int, C.c_int)
        self.next = fw.fn('ringnav_next', C.c_int, C.c_int)
        self.prev = fw.fn('ringnav_prev', C.c_int)
        self.clear = fw.fn('playback_clear', None)
        self.upcoming = fw.fn('playback_upcoming', C.c_uint, C.POINTER(C.c_uint), C.c_uint)

    def song(self, path, album=b'', artist=b'', disc=0, track=0, cue=(0, 0), kind=8):
        record = C.create_string_buffer(0x60)
        for offset, value in ((O['REC_PATH'], path), (O['REC_ALBUM'], album), (O['REC_ALBUM_ARTIST'], artist)):
            chars = C.create_string_buffer(value)
            self.keep.append(chars)
            struct.pack_into('<Q', record, offset, C.addressof(chars))
        for offset, value in ((O['REC_TYPE'], kind), (O['REC_DISC'], disc), (O['REC_TRACK'], track),
                              (O['REC_CUE_START'], cue[0]), (O['REC_CUE_END'], cue[1])):
            struct.pack_into('<i', record, offset, value)
        self.keep.append(record)
        return C.addressof(record)

    def albums(self, *sizes):
        """Songs of albums "A", "B"... of these sizes, shuffled together: the queue's index gives neither order."""
        rows = [self.song(b'/m/%c/%02d.flac' % (65 + a, t), b'%c' % (65 + a), track=t + 1)
                for a, size in enumerate(sizes) for t in range(size)]
        random.Random(len(rows)).shuffle(rows)
        return rows

    def load(self, rows, pos=0):
        self.deques[self.handle][:] = rows
        self.mcl.pos = pos
        return rows

    def rng(self, seed):
        self.fw.var('playback__rng', C.c_uint).value = seed

    def album(self, i):
        """The album index i's song is in, by its record."""
        return C.string_at(struct.unpack_from('<Q', C.string_at(self.deques[self.handle][i], 0x60), O['REC_ALBUM'])[0])

    def track(self, i):
        return struct.unpack_from('<i', C.string_at(self.deques[self.handle][i], 0x60), O['REC_TRACK'])[0]

    def order(self):
        return [self.s.order[i].index for i in range(self.s.n)]

    def cycle(self):
        return [self.s.cycle[i].index for i in range(self.s.n)]

    def seen(self):
        return {i for i in range(self.s.n) if self.s.seen[i]}

    def pending(self):
        return list(self.s.pend[:self.s.npend])

    def history(self):
        return list(self.s.history[:self.s.hn])

    def up_next(self, limit=64):
        out = (C.c_uint * limit)()
        return list(out[:self.upcoming(out, limit)])

    def run(self, limit):
        """The tracks automatic nexts play, until the queue stops or limit of them."""
        played = []
        while len(played) < limit and self.next(1) >= 0:
            played.append(self.mcl.pos)
        return played


@pytest.fixture
def player(fw, mcl):
    return Player(fw, mcl)


def plan(player, order, at=None, cursor=0, seen=(), pend=(), groups=None, cycle=None, repeat=ONE_PASS, shuffle=0):
    """Advanced play with this order of queue indices, in tables the size rebuild allocates."""
    fw, s, n = player.fw, player.s, len(order)
    calloc = fw.fn('calloc', C.c_void_p, C.c_size_t, C.c_size_t)
    table = lambda kind, count: C.cast(calloc(count, C.sizeof(kind)), C.POINTER(kind))
    s.order, s.cycle, s.groups, s.seen, s.history = (table(Entry, n + 1), table(Entry, n + 1), table(C.c_uint, n + 1),
                                                     table(C.c_uint, n + 1), table(C.c_uint, 4096))
    for i, index in enumerate(order):
        s.order[i].index = index
        s.cycle[i].index = (cycle or order)[i]
        s.groups[i] = groups[i] if groups else 0
    for i in seen:
        s.seen[i] = 1
    s.active, s.repeat, s.shuffle, s.n, s.cursor, s.npend = 1, repeat, shuffle, n, cursor, len(pend)
    s.pend[:len(pend)] = list(pend)
    player.mcl.pos = order[cursor] if at is None else at
    player.deques[player.handle][:] = [0] * n
    return player


# Play Next's pending list.
@pytest.fixture
def pend(fw):
    return (fw.var('playback__s', State), fw.fn('playback__pend_find', C.c_uint, C.c_uint),
            fw.fn('playback__pend_front', None, C.c_uint), fw.fn('playback__pend_take', C.c_int, C.c_uint))


def test_pend_find_gives_the_place_or_the_end(pend):
    s, find, _, _ = pend
    assert find(7) == 0
    s.pend[:3], s.npend = [7, 9, 4], 3
    assert [find(x) for x in (7, 9, 4, 5)] == [0, 1, 2, 3]
    s.npend = 1
    assert find(9) == 1  # a stale entry past npend isn't listed


def test_pend_front_puts_the_newest_first_without_a_duplicate(pend):
    s, _, front, _ = pend
    for x in (1, 2, 3, 1, 1):
        front(x)
    assert list(s.pend[:s.npend]) == [1, 3, 2]


def test_pend_front_of_a_full_list(pend):
    s, _, front, _ = pend
    s.pend[:64], s.npend = list(range(64)), 64
    front(100)
    assert s.npend == 64 and list(s.pend) == [100] + list(range(63))
    front(62)  # already listed: nothing else drops
    assert s.npend == 64 and list(s.pend) == [62, 100] + list(range(62))


def test_pend_take(pend):
    s, _, _, take = pend
    assert take(5) == 0
    s.pend[:4], s.npend = [1, 2, 3, 4], 4
    assert take(2) == 1 and list(s.pend[:s.npend]) == [1, 3, 4]
    assert take(4) == 1 and list(s.pend[:s.npend]) == [1, 3]
    assert take(4) == 0 and s.npend == 2
    assert take(1) == 1 and take(3) == 1 and s.npend == 0


def test_pend_take_the_last_of_a_full_list(pend):
    s, _, _, take = pend
    s.pend[:64], s.npend = list(range(64)), 64
    assert take(63) == 1 and list(s.pend[:s.npend]) == list(range(63))
    assert take(0) == 1 and list(s.pend[:s.npend]) == list(range(1, 63))


def test_pending_list_against_a_model(pend):
    s, find, front, take = pend
    rng, model = random.Random(0x50e4d), []
    for _ in range(3000):
        x = rng.randrange(80)
        if rng.random() < .6:
            front(x)
            model = [x] + [v for v in model if v != x]
            del model[64:]
        else:
            assert take(x) == (x in model)
            model = [v for v in model if v != x]
        assert list(s.pend[:s.npend]) == model and find(x) == (model.index(x) if x in model else len(model))


# What plays next.
def test_nothing_upcoming_without_advanced_play(player):
    plan(player, [0, 1, 2])
    player.s.active = 0
    assert player.up_next() == []


def test_nothing_upcoming_past_the_queue(player):
    plan(player, [0, 1, 2], at=3)
    assert player.up_next() == []


def test_upcoming_walks_the_order_past_the_cursor(player):
    plan(player, [3, 1, 0, 2], cursor=1, seen=(3, 1))
    assert player.up_next() == [0, 2]


def test_upcoming_skips_what_has_played(player):
    plan(player, [3, 1, 0, 2], cursor=1, seen=(3, 1, 0))
    assert player.up_next() == [2]


def test_play_next_comes_first_and_only_once(player):
    plan(player, [0, 1, 2, 3], pend=(3, 0))  # 0 is the playing track's own, played again: a Play Next of it
    assert player.up_next() == [3, 0, 1, 2]


def test_a_pass_ends_with_its_order(player):
    plan(player, [0, 1, 2], cursor=2, seen=(0, 1, 2))
    assert player.up_next() == []


def test_repeat_all_runs_on_into_the_next_pass(player):
    plan(player, [0, 1, 2], cursor=1, seen=(0, 1), cycle=[2, 0, 1], repeat=REPEAT_ALL)
    assert player.up_next() == [2, 2, 0, 1]


@pytest.mark.parametrize('repeat, expected', [(ONE_GROUP, [1]), (REPEAT_GROUP, [1, 1, 0])])
def test_a_group_repeat_keeps_to_the_group(player, repeat, expected):
    plan(player, [0, 1, 2, 3], groups=[0, 0, 2, 2], cycle=[3, 1, 2, 0], pend=(2,), repeat=repeat, seen=(0,))
    assert player.up_next() == expected


@pytest.mark.parametrize('repeat', [ONE_SONG, REPEAT_SONG])
def test_up_next_is_empty_where_a_natural_end_has_no_next(player, repeat):
    plan(player, [0, 1, 2], repeat=repeat)
    assert player.up_next() == []


@pytest.mark.parametrize('repeat', [ONE_SONG, REPEAT_SONG])
def test_the_peek_itself_ignores_the_single_song_rules(fw, player, repeat):
    plan(player, [0, 1, 2], repeat=repeat, seen=(0,), pend=(2,))
    out = (C.c_uint * 4)()
    assert fw.fn('playback__upcoming', C.c_uint, C.POINTER(C.c_uint), C.c_uint)(out, 4) >= 2 and out[0] == 2 and out[1] == 1


@pytest.mark.parametrize('limit', range(5))
def test_upcoming_writes_at_most_its_room(fw, player, limit):
    plan(player, [0, 1, 2, 3], pend=(3,), cycle=[1, 2, 3, 0], repeat=REPEAT_ALL, seen=(0,))
    full = [3, 1, 2, 1, 2, 3, 0]
    out = (C.c_uint * 8)(*[99] * 8)
    assert player.upcoming(out, limit) == limit
    assert list(out) == full[:limit] + [99] * (8 - limit)


def test_a_manual_next_skips_the_single_song_rules(fw, player):
    successor = fw.fn('playback_successor', C.c_int, C.c_int)
    plan(player, [0, 1, 2], repeat=ONE_SONG, seen=(0,))
    assert successor(0) == 1 and successor(1) == -1
    plan(player, [0, 1, 2], repeat=REPEAT_SONG, seen=(0,))
    player.s.next_valid = 0
    assert successor(0) == 1 and successor(1) == 0


def test_a_natural_end_at_the_last_track_stops(fw, player):
    successor = fw.fn('playback_successor', C.c_int, C.c_int)
    plan(player, [2, 0, 1], cursor=2, seen=(2, 0, 1))
    assert successor(1) == -1 and successor(0) == -1


def test_successor_without_advanced_play(fw, player):
    successor = fw.fn('playback_successor', C.c_int, C.c_int)
    plan(player, [0, 1, 2], repeat=REPEAT_SONG)
    player.s.active = 0
    assert successor(1) == -1 and successor(0) == -1


def test_the_automatic_answer_is_kept_until_the_track_changes(fw, player):
    successor = fw.fn('playback_successor', C.c_int, C.c_int)
    plan(player, [0, 1, 2, 3], seen=(0,))
    assert successor(1) == 1
    player.s.order[1].index, player.s.order[2].index = 2, 1
    assert successor(1) == 1  # the preload's answer, while this track plays
    player.mcl.pos = 3
    assert successor(1) == 2 and player.s.next_pos == 3


def test_peeking_changes_nothing(fw, player):
    successor = fw.fn('playback_successor', C.c_int, C.c_int)
    plan(player, [2, 0, 3, 1], cursor=1, seen=(2, 0), pend=(1,), repeat=REPEAT_ALL)
    before = player.order(), player.cycle(), player.seen(), player.pending(), player.history(), player.s.cursor
    player.up_next(), successor(0), successor(1)
    assert (player.order(), player.cycle(), player.seen(), player.pending(), player.history(), player.s.cursor) == before
    assert not fw.calls('toolsRandnum')


@pytest.mark.parametrize('order, cursor, pick, expected, new_cursor', [
    ([0, 1, 2, 3, 4], 1, 4, [0, 1, 4, 2, 3], 1),      # later: right after the cursor
    ([0, 1, 2, 3, 4], 1, 2, [0, 1, 2, 3, 4], 1),      # already next
    ([0, 1, 2, 3, 4], 3, 0, [1, 2, 3, 0, 4], 2),      # earlier: the current stays current
    ([0, 1, 2, 3, 4], 3, 2, [0, 1, 3, 2, 4], 2),
    ([0, 1, 2], 2, 0, [1, 2, 0], 1),                  # from the last place
    ([4, 3, 2, 1, 0], 0, 0, [4, 0, 3, 2, 1], 0),
    ([0, 1, 2], 1, 1, [0, 1, 2], 1),                  # the current itself: nowhere to move
])
def test_play_now_moves_the_pick_right_after_the_cursor(fw, player, order, cursor, pick, expected, new_cursor):
    plan(player, order, cursor=cursor, seen=(pick,))
    fw.fn('playback__play_now', None, C.c_uint)(pick)
    assert player.order() == expected and player.s.cursor == new_cursor and pick not in player.seen()


def test_play_now_keeps_every_track(fw, player):
    rng = random.Random(0xa11)
    play_now = fw.fn('playback__play_now', None, C.c_uint)
    for _ in range(400):
        n = rng.randrange(1, 12)
        order = rng.sample(range(n), n)
        cursor, pick = rng.randrange(n), rng.randrange(n)
        plan(player, order, cursor=cursor)
        current = order[cursor]
        play_now(pick)
        got = player.order()
        assert sorted(got) == list(range(n)) and got[player.s.cursor] == current
        if pick != current:
            assert got[player.s.cursor + 1] == pick
        rest = [x for x in order if x != pick]
        assert [x for x in got if x != pick] == rest


def test_a_track_played_now_is_not_skipped_as_seen(fw, player):
    plan(player, [0, 1, 2, 3], cursor=2, seen=(0, 1, 2))
    fw.fn('playback__play_now', None, C.c_uint)(0)
    assert player.up_next() == [0, 3]


# The order itself.
def rand_below(fw, n, seed=None):
    if seed is not None:
        fw.var('playback__rng', C.c_uint).value = seed
    return fw.fn('rand_below', C.c_uint, C.c_uint)(n)


def xorshift32(x):
    x ^= x << 13 & 0xffffffff
    x ^= x >> 17
    return x ^ x << 5 & 0xffffffff


def test_rand_below_is_xorshift32(fw):
    x = 1
    for _ in range(50):
        x = xorshift32(x)
        assert rand_below(fw, 0xffffffff) == x % 0xffffffff if _ else rand_below(fw, 0xffffffff, 1) == x % 0xffffffff


def test_rand_below_is_seeded_once_and_never_zero(fw):
    fw.mock('toolsRandnum', lambda *_: 0x7ffffffe)
    below = fw.fn('rand_below', C.c_uint, C.c_uint)
    values = [below(1000) for _ in range(200)]
    assert len(fw.calls('toolsRandnum')) == 1
    assert fw.var('playback__rng', C.c_uint).value and all(v < 1000 for v in values) and len(set(values)) > 50
    assert below(1) == 0


def test_groups_are_albums_named_alike_and_their_smallest_index(player):
    rows = player.albums(3, 2, 1)
    player.load(rows)
    assert player.set(1, ONE_PASS) == 1
    for i in range(6):
        same = [j for j in range(6) if player.album(j) == player.album(i)]
        assert player.s.groups[i] == min(same)


def test_an_album_is_its_name_case_aside_and_its_folder(player):
    rows = [player.song(b'/m/z/1.flac', b'Same'), player.song(b'/m/z/2.flac', b'SAME'), player.song(b'/m/y/3.flac', b'Same')]
    player.load(rows)
    assert player.set(1, ONE_PASS) == 1
    assert [player.s.groups[i] for i in range(3)] == [0, 0, 2]


def test_an_ordered_queue_plays_album_by_album_in_track_order(player):
    player.load(player.albums(3, 2, 2))
    assert player.set(1, ONE_PASS) == 1
    order = player.order()
    assert sorted(order) == list(range(7))
    keys = [(player.s.groups[i], player.track(i)) for i in order]
    assert keys == sorted(keys)


def test_folders_group_as_album_tags_do_not(player):
    rows = [player.song(b'/m/a/1.flac', b'X'), player.song(b'/m/b/1.flac', b'X'), player.song(b'/m/a/2.flac', b'Y')]
    player.load(rows)
    assert player.set(2, 1) == 1
    assert [player.s.groups[i] for i in range(3)] == [0, 1, 0]


def check_order(player, shuffle, pos):
    order, cycle, n = player.order(), player.cycle(), player.s.n
    assert sorted(order) == sorted(cycle) == list(range(n))
    assert order[player.s.cursor] == pos and pos in player.seen()
    assert {order[i] for i in range(player.s.cursor + 1)} <= player.seen() or shuffle == 1
    group = lambda i: player.s.groups[i]
    for pass_order in (order, cycle) if shuffle >= 2 else ():
        runs = [group(pass_order[0])]
        for i in pass_order[1:]:
            if group(i) != runs[-1]:
                runs.append(group(i))
        assert len(runs) == len(set(runs))  # a group's tracks stay together
    for pass_order in (order, cycle) if shuffle == 3 else ():
        for g in set(map(group, order)):
            tracks = [player.track(i) for i in pass_order if group(i) == g]
            assert tracks == sorted(tracks)  # Shuffle Albums keeps each album's own order
    if shuffle >= 3:
        assert group(order[0]) == group(pos)  # the current category finishes first


@pytest.mark.parametrize('shuffle', range(5))
@pytest.mark.parametrize('seed', SEEDS)
def test_every_shuffle_mode_is_a_whole_order_around_the_current_track(player, shuffle, seed):
    player.rng(seed)
    for pos in range(9):
        player.load(player.albums(4, 3, 2), pos)
        player.clear()
        assert player.set(0, shuffle) == 1
        check_order(player, shuffle, pos)


def test_shuffle_songs_really_shuffles(player):
    player.load(player.albums(10, 10, 10))
    orders = set()
    for seed in SEEDS:
        player.rng(seed)
        player.clear()
        assert player.set(0, 1) == 1
        orders.add(tuple(player.order()))
    assert len(orders) == len(SEEDS)


@pytest.mark.parametrize('shuffle', range(5))
@pytest.mark.parametrize('seed', SEEDS)
def test_up_next_is_what_then_plays(player, shuffle, seed):
    player.rng(seed)
    player.load(player.albums(3, 3, 3), 4)
    assert player.set(0, shuffle) == 1
    promised = player.up_next()
    assert player.run(20) == promised


@pytest.mark.parametrize('shuffle', range(5))
def test_a_pass_plays_every_track_once(player, shuffle):
    player.load(player.albums(3, 4, 2))
    assert player.set(0, shuffle) == 1
    first = player.mcl.pos = player.order()[0]
    player.clear()
    assert player.set(0, shuffle) == 1 and player.s.cursor == 0
    assert sorted([first] + player.run(100)) == list(range(9))


@pytest.mark.parametrize('shuffle', range(5))
def test_repeat_all_plays_whole_passes_for_ever(player, shuffle):
    player.load(player.albums(2, 3, 2))
    assert player.set(0, shuffle) == 1
    first = player.mcl.pos = player.order()[0]
    player.clear()
    assert player.set(0, shuffle) == 1 and player.set(1, REPEAT_ALL) == 1 and player.s.cursor == 0
    played = [first] + player.run(7 * 4 - 1)
    assert len(played) == 28
    for at in range(0, 28, 7):
        assert sorted(played[at:at + 7]) == list(range(7))


@pytest.mark.parametrize('shuffle', range(5))
def test_repeat_group_stays_in_the_album(player, shuffle):
    player.load(player.albums(3, 4, 2), 2)
    assert player.set(0, shuffle) == 1 and player.set(1, REPEAT_GROUP) == 1
    group = player.s.groups[2]
    size = sum(player.s.groups[i] == group for i in range(9))
    played = player.run(size * 3)
    assert len(played) == size * 3 and all(player.s.groups[i] == group for i in played)
    assert sorted(set(played)) == [i for i in range(9) if player.s.groups[i] == group]


def test_repeat_song_repeats_automatically_and_next_moves_on(player):
    player.load(player.albums(2, 2))
    assert player.set(1, REPEAT_SONG) == 1
    at = player.mcl.pos
    assert player.run(3) == [at] * 3
    assert player.next(0) >= 0 and player.mcl.pos == player.order()[player.s.cursor] != at


def test_play_one_song_stops_after_it(player):
    player.load(player.albums(2, 2))
    assert player.set(1, ONE_SONG) == 1
    assert player.next(1) == -1 and len(player.fw.calls('mclStop')) == 1


def test_a_preloaded_next_goes_through_stock(player):
    player.load(player.albums(2, 2))
    assert player.set(0, 0) == 1
    expected = player.up_next()[0]
    player.mcl.prepos, player.mcl.preload = expected, 1
    player.fw.mock('stock_next_trampoline', lambda *_: 5)
    assert player.next(1) == 5
    assert not player.fw.calls('mclStartPlayer')


def test_ringnav_next_without_advanced_play_is_stock(player):
    player.load(player.albums(2, 2), 1)
    player.fw.mock('stock_next_trampoline', lambda automatic, *_: 40 + automatic)
    assert player.next(1) == 41 and player.next(0) == 40


def test_list_play_stops_at_a_library_queues_end(player):
    player.load(player.albums(2, 2), 3)
    player.mcl.type, player.mcl.mode = LIBRARY, 0
    assert player.next(0) == -1 and player.next(1) == -1
    assert len(player.fw.calls('mclStop')) == 2 and not player.fw.calls('stock_next_trampoline')


@pytest.mark.parametrize('kind, mode, pos', [(FOLDER, 0, 3), (LIBRARY, 3, 3), (LIBRARY, 0, 2)])
def test_list_end_is_only_list_plays_last_library_track(player, kind, mode, pos):
    player.load(player.albums(2, 2), pos)
    player.mcl.type, player.mcl.mode = kind, mode
    player.next(0)
    assert not player.fw.calls('mclStop') and len(player.fw.calls('stock_next_trampoline')) == 1


def test_autonexts_count_only_natural_ends(fw, player):
    player.load(player.albums(1, 1))
    player.next(1), player.next(0), player.next(1)
    assert fw.var('auto_nexts', C.c_uint).value == 2


def test_playback_set_refuses_what_is_out_of_range(player):
    player.load(player.albums(2))
    for kind, value in ((-1, 0), (3, 0), (0, -1), (0, 5), (1, 6), (2, 2)):
        assert player.set(kind, value) == 0
    assert player.s.active == 0
    for kind, value in ((0, 4), (1, 5), (2, 1)):
        assert player.set(kind, value) == 1


def test_options_read_as_defaults_without_advanced_play(fw, player):
    option = fw.fn('playback_option', C.c_int, C.c_int)
    assert [option(k) for k in range(3)] == [0, 2, 0]
    player.load(player.albums(2))
    player.set(0, 3), player.set(1, 4), player.set(2, 1)
    assert [option(k) for k in range(3)] == [3, 4, 1]
    player.clear()
    assert [option(k) for k in range(2)] == [0, 2]


def test_setting_the_same_option_rebuilds_nothing(player):
    player.load(player.albums(3, 3))
    player.rng(99)
    assert player.set(0, 1) == 1
    order = player.order()
    assert player.set(0, 1) == 1 and player.order() == order


def test_a_too_long_queue_ends_advanced_play(player):
    player.load([0] * 65537)
    assert player.set(0, 1) == 0 and player.s.active == 0


def test_next_after_stocks_own_delete_stays_on_the_queue(player):
    player.load(player.albums(5))
    assert player.set(1, REPEAT_ALL) == 1
    player.deques[player.handle].pop()
    assert all(i < 4 for i in player.run(4))


def test_a_new_setting_drops_the_pending_list_and_the_preload(player):
    player.load(player.albums(3, 3))
    assert player.set(0, 1) == 1
    player.s.pend[0], player.s.npend = 3, 1
    player.mcl.preload, player.mcl.prepos, player.mcl.fd = 1, 4, 9
    assert player.set(1, REPEAT_ALL) == 1
    assert player.s.npend == 0 and player.mcl.preload == 0 and player.mcl.prepos == -1
    assert [c[1][0] for c in player.fw.calls('fw_send')] == [9]


def test_close_preload_sends_only_with_a_preload_and_a_socket(fw, mcl):
    close = fw.fn('playback__close_preload', None)
    mcl.preload, mcl.fd = 0, 5
    close()
    mcl.preload, mcl.fd = 1, -1
    close()
    assert not fw.calls('fw_send') and mcl.prepos == -1 and mcl.preload == 0


def test_previous_goes_back_and_next_returns_to_what_it_left(player):
    player.load(player.albums(5))
    assert player.set(0, 1) == 1
    first = player.mcl.pos
    played = player.run(2)
    assert player.prev() == 0 and player.mcl.pos == played[0] and player.history() == [first, played[0]]
    assert player.up_next()[0] == played[1]
    assert player.run(1) == [played[1]]


def test_previous_at_the_start_stays(player):
    player.load(player.albums(3), 1)
    assert player.set(0, 1) == 1
    assert player.prev() == 1 and player.mcl.pos == 1 and not player.fw.calls('mclStartPlayer')


def test_history_keeps_the_last_4096(fw, player):
    plan(player, [0, 1])
    history = fw.fn('playback__history', None, C.c_uint)
    player.s.resume_key = 7
    for i in range(4100):
        history(i)
    assert player.s.hn == 4096 and player.history() == list(range(4, 4100)) and player.s.resume_key == 0


def test_a_pick_in_a_shuffle_plays_on_and_comes_no_more(fw, player):
    start = fw.fn('ringnav_start_queue', C.c_int, C.c_int)
    fw.mock('stock_start_queue_trampoline', lambda at, *_: 20 + at)
    player.load(player.albums(4, 4))
    player.rng(5)
    assert player.set(0, 1) == 1 and player.set(1, ONE_PASS) == 1
    pos, order = player.mcl.pos, player.order()
    pick = order[-2]
    assert start(pick) == 20 + pick
    player.mcl.pos = pick
    rest = player.run(20)
    assert sorted([pick, pos] + rest) == list(range(8))
    assert rest == [i for i in order if i not in (pos, pick)]


def test_a_pick_in_an_ordered_queue_goes_on_from_there(fw, player):
    start = fw.fn('ringnav_start_queue', C.c_int, C.c_int)
    player.load(player.albums(6))
    assert player.set(0, 0) == 1
    order = player.order()
    start(order[3])
    player.mcl.pos = order[3]
    assert player.run(9) == order[4:]


def test_a_pick_inside_play_next_takes_only_its_entry(fw, player):
    start = fw.fn('ringnav_start_queue', C.c_int, C.c_int)
    player.load(player.albums(6))
    assert player.set(0, 1) == 1
    player.mcl.pos = player.order()[player.s.cursor]
    order = player.order()
    player.s.pend[:3], player.s.npend = [order[3], order[4], order[5]], 3
    start(order[4])
    assert player.pending() == [order[3], order[5]]


@pytest.mark.parametrize('at', [-1, 6, 3])
def test_stock_picks_it_cannot_follow_change_nothing(fw, player, at):
    start = fw.fn('ringnav_start_queue', C.c_int, C.c_int)
    player.load(player.albums(6), 3)
    assert player.set(0, 1) == 1
    order, history = player.order(), player.history()
    start(at)
    assert player.order() == order and player.history() == history
    assert len(fw.calls('stock_start_queue_trampoline')) == 1


def test_a_pick_after_stocks_delete_plays_on_over_the_queue_left(fw, player):
    start = fw.fn('ringnav_start_queue', C.c_int, C.c_int)
    player.load(player.albums(6))
    assert player.set(0, 1) == 1 and player.set(1, ONE_PASS) == 1
    player.deques[player.handle].pop()
    start(2)
    assert player.s.n == 5 and player.history() == [0, 2]
    player.mcl.pos = 2
    assert sorted(player.run(10)) == [1, 3, 4]


def test_stocks_delete_drops_play_next(player):
    player.load(player.albums(5))
    assert player.set(0, 0) == 1
    player.s.pend[0], player.s.npend = 4, 1
    player.deques[player.handle].pop()
    assert player.next(0) >= 0 and player.mcl.pos < 4 and player.pending() == []


def test_the_poll_follows_stocks_delete_before_crossfade_looks(fw, player):
    player.load(player.albums(5))
    assert player.set(0, 1) == 1
    player.deques[player.handle].pop()
    fw.fn('playback_poll', None)()
    assert player.s.n == 4 and player.s.active == 1


def test_previous_after_stocks_delete_stays_on_the_queue(player):
    player.load(player.albums(5))
    assert player.set(0, 1) == 1 and player.set(1, REPEAT_ALL) == 1
    while player.mcl.pos != 4:
        player.next(0)
    player.next(0)             # Previous would go back to 4
    player.deques[player.handle].pop()
    player.prev()
    assert player.s.n == 4 and player.mcl.pos < 4


def album(player, n):
    """One album's n tracks in queue order, playing in that order; a track's number names it after a delete."""
    player.load([player.song(b'/m/A/%02d.flac' % t, b'A', track=t) for t in range(1, n + 1)])
    assert player.set(0, 0) == 1 and player.set(1, ONE_PASS) == 1


def delete(player, at):
    """Stock's View Queue delete: the row goes; a position past it moves down with it."""
    player.deques[player.handle].pop(at)
    if player.mcl.pos > at:
        player.mcl.pos -= 1


def tracks(player, indices):
    return [player.track(i) for i in indices]


def test_stocks_delete_of_a_middle_row_keeps_play_next_and_previous(fw, player):
    album(player, 6)
    player.run(2)  # 1, 2, 3 played
    player.s.pend[0], player.s.npend = 5, 1  # Play Next: track 6
    delete(player, 1)  # track 2
    fw.fn('playback_poll', None)()
    assert player.s.n == 5 and tracks(player, player.history()) == [1, 3] and tracks(player, player.pending()) == [6]
    assert player.next(0) >= 0 and player.track(player.mcl.pos) == 6
    assert player.prev() == 0 and player.track(player.mcl.pos) == 3
    assert player.prev() == 0 and player.track(player.mcl.pos) == 1


def test_stocks_delete_of_the_playing_row_goes_back_to_the_one_before(fw, player):
    album(player, 5)
    player.run(2)  # 1, 2, 3, playing 3
    delete(player, 2)  # stock's position now names track 4
    assert player.prev() == 0 and player.track(player.mcl.pos) == 2
    assert tracks(player, player.history()) == [1, 2] and player.run(9) == [2, 3]  # tracks 4 and 5 left


@pytest.mark.parametrize('key', ['prev', 'next'])
def test_stocks_delete_of_the_last_row_while_it_plays(fw, player, key):
    album(player, 4)
    player.run(3)  # playing track 4, the last
    delete(player, 3)
    assert player.mcl.pos == 3 == len(player.deques[player.handle])
    if key == 'prev':  # back to the one before it, not two back
        assert player.prev() == 0 and player.track(player.mcl.pos) == 3 and player.s.n == 3
    else:  # nothing after it: the queue ends, as at its own end
        assert player.next(0) == -1 and fw.calls('mclStop')


def test_stocks_delete_off_the_ui_thread_waits_for_its_pass(fw, player):
    poll = fw.fn('playback_poll', None)
    album(player, 5)
    poll()  # the UI thread: pthread_self's 0 here
    player.s.pend[0], player.s.npend = 4, 1
    delete(player, 1)
    fw.mock('fw_pthread_self', lambda *_: 7)  # a headset key's thread
    fw.mock('stock_next_trampoline', lambda *_: 33)
    assert player.next(0) == 33 and player.s.n == 5 and player.pending() == [4]  # stock's way, nothing touched
    fw.mock('fw_pthread_self', lambda *_: 0)
    poll()
    assert player.s.n == 4 and player.pending() == [3]


def test_a_queue_stock_grew_gets_a_new_order(fw, player):
    album(player, 4)
    player.run(1)
    player.s.pend[0], player.s.npend = 3, 1
    player.deques[player.handle].append(player.song(b'/m/A/05.flac', b'A', track=5))
    fw.fn('playback_poll', None)()
    assert player.s.n == 5 and player.pending() == [] and player.history() == [1]


def test_move_makes_a_track_next(fw, player):
    move = fw.fn('playback_move', C.c_int, C.c_uint)
    player.load(player.albums(6))
    assert player.set(0, 1) == 1
    pick = player.up_next()[-1]
    assert move(pick) == pick and player.up_next()[0] == pick and player.pending() == [pick]
    assert len(player.up_next()) == 5


@pytest.mark.parametrize('case', ['inactive', 'current', 'past', 'resized'])
def test_move_refuses(fw, player, case):
    move = fw.fn('playback_move', C.c_int, C.c_uint)
    player.load(player.albums(6), 2)
    assert player.set(0, 1) == 1
    if case == 'inactive':
        player.clear()
    if case == 'resized':
        player.deques[player.handle].pop()
    assert move({'current': 2, 'past': 6}.get(case, 1)) == -1 and player.pending() == []


def test_an_insert_remaps_every_index(fw, player):
    insert = fw.fn('playback_insert', None, C.c_uint, C.c_uint, C.c_int)
    rows = player.load(player.albums(3, 3))
    assert player.set(0, 1) == 1
    played = player.run(2)
    playing = player.mcl.pos
    pending, seen, history = rows[5], {rows[i] for i in player.seen()}, [rows[i] for i in player.history()]
    player.s.pend[0], player.s.npend = rows.index(pending), 1
    at, new = playing + 1, [player.song(b'/m/N/%d.flac' % i, b'N', track=i + 1) for i in range(2)]
    rows[at:at] = new
    player.load(rows, playing)
    insert(at, 2, 1)
    assert [rows[i] for i in player.pending()] == new + [pending]
    assert {rows[i] for i in player.seen()} >= seen and [rows[i] for i in player.history()] == history
    assert player.up_next()[:3] == [at, at + 1, rows.index(pending)]


def test_an_insert_without_advanced_play_only_marks_the_snapshot_stale(fw, player):
    fw.fn('playback_insert', None, C.c_uint, C.c_uint, C.c_int)(0, 3, 1)
    assert player.s.dirty == 1 and player.s.n == 0 and not fw.calls('fw_send')


def queue_insert(fw, player, add, next_):
    pool = player.deques.new()
    C.c_uint64.from_address(O['MCL_POOL']).value = pool
    return pool, fw.fn('playback_queue_insert', C.c_uint, C.c_void_p, C.c_void_p, C.c_int)(
        player.handle, player.deques.new(add), next_)


def test_queue_insert_shifts_the_shuffle_pool_and_last_index(fw, player, mcl):
    rows = player.load(player.albums(5), 1)
    mcl.lastpos = 3
    held = (C.c_int * 3)(0, 2, 4)
    pool = player.deques.new([C.addressof(held) + 4 * i for i in range(3)])
    C.c_uint64.from_address(O['MCL_POOL']).value = pool
    add = [player.song(b'/m/N/1.flac'), player.song(b'/m/N/2.flac')]
    assert fw.fn('playback_queue_insert', C.c_uint, C.c_void_p, C.c_void_p, C.c_int)(player.handle, player.deques.new(add), 1) == 2
    assert player.deques[player.handle] == rows[:2] + add + rows[2:]
    assert list(held) == [0, 4, 6] and player.deques[pool][3:] == [2, 3] and mcl.lastpos == 5


def test_queue_add_goes_to_the_end(fw, player, mcl):
    rows = player.load(player.albums(3), 0)
    mcl.lastpos = -1
    add = [player.song(b'/m/N/1.flac')]
    pool, at = queue_insert(fw, player, add, 0)
    assert at == 3 and player.deques[player.handle] == rows + add and mcl.lastpos == -1
    assert player.deques[pool] == [3]


def test_queue_insert_at_a_position_past_the_queue_appends(fw, player, mcl):
    rows = player.load(player.albums(2), 7)
    add = [player.song(b'/m/N/1.flac')]
    _, at = queue_insert(fw, player, add, 1)
    assert at == 2 and player.deques[player.handle] == rows + add


def test_queue_insert_closes_only_a_preload_it_makes_stale(fw, player, mcl):
    player.load(player.albums(4), 1)
    mcl.preload, mcl.fd = 1, 3
    queue_insert(fw, player, [player.song(b'/m/N/1.flac')], 0)
    assert mcl.preload == 1 and not fw.calls('fw_send')
    queue_insert(fw, player, [player.song(b'/m/N/2.flac')], 1)
    assert mcl.preload == -1 and len(fw.calls('fw_send')) == 1


def test_group_skip_forward_goes_to_the_next_album(fw, player):
    skip = fw.fn('playback_group_skip', C.c_int, C.c_int)
    player.load(player.albums(3, 3, 3))
    assert player.set(0, 0) == 1
    order = player.order()
    player.clear(), player.load(player.deques[player.handle], order[0])
    assert player.set(0, 0) == 1
    group = player.s.groups[player.mcl.pos]
    assert skip(1) == 1 and player.s.groups[player.mcl.pos] != group
    assert player.mcl.pos == order[3] and player.track(player.mcl.pos) == 1


def test_group_skip_forward_at_the_last_album(fw, player):
    skip = fw.fn('playback_group_skip', C.c_int, C.c_int)
    rows = player.load(player.albums(2, 2))
    assert player.set(0, 0) == 1
    last = player.order()[-1]
    player.clear(), player.load(rows, last)
    assert player.set(0, 0) == 1
    assert skip(1) == 0 and player.mcl.pos == last
    assert player.set(1, REPEAT_ALL) == 1
    assert skip(1) == 1 and player.s.groups[player.mcl.pos] != player.s.groups[last]


def test_group_skip_back_returns_to_the_last_albums_start(fw, player):
    skip = fw.fn('playback_group_skip', C.c_int, C.c_int)
    rows = player.load(player.albums(3, 3))
    assert player.set(0, 0) == 1
    order = player.order()
    player.clear(), player.load(rows, order[0])
    assert player.set(0, 0) == 1
    player.run(4)
    assert player.mcl.pos == order[4]
    assert skip(0) == 1 and player.mcl.pos == order[0] and player.seen() >= {order[0]}
    assert not player.seen() & set(order[1:3]) and player.history()[-1] == order[0]


def test_group_skip_back_with_nothing_before(fw, player):
    skip = fw.fn('playback_group_skip', C.c_int, C.c_int)
    player.load(player.albums(3, 3))
    assert player.set(0, 0) == 1
    assert skip(0) == 0


def test_group_skip_starts_advanced_play_when_off(fw, player):
    skip = fw.fn('playback_group_skip', C.c_int, C.c_int)
    player.load(player.albums(2, 2))
    assert skip(1) == 1 and player.s.active == 1


# A load's videos.
def no_videos(fw, player, rows, at):
    where = C.c_int(at)
    out = fw.fn('playback__no_videos', C.c_long, C.c_void_p, C.POINTER(C.c_int))(player.deques.new(rows), where)
    return (player.deques[out] if out else None), where.value


@pytest.mark.parametrize('kinds, at, kept, new_at', [
    ('aaa', 1, None, 1),
    ('ava', 2, [0, 2], 1),      # the same track
    ('ava', 1, [0, 2], 1),      # a video: the next song after it
    ('vaa', 0, [1, 2], 0),
    ('vaa', 2, [1, 2], 1),
    ('aav', 2, [0, 1], 1),      # a last video has no next song
    ('vvv', 1, [], 0),
])
def test_no_videos_keeps_the_songs_and_the_place(fw, player, kinds, at, kept, new_at):
    rows = [player.song(b'/m/%d.%s' % (i, b'mp4' if k == 'v' else b'flac')) for i, k in enumerate(kinds)]
    got, where = no_videos(fw, player, rows, at)
    assert got == (None if kept is None else [rows[i] for i in kept]) and where == new_at


@pytest.mark.parametrize('path, kind, video', [
    (b'/a/b.mp4', 8, 1), (b'/a/b.MKV', 8, 1), (b'/a/b.m4v', 8, 1), (b'/a/b.flac', 8, 0), (b'/a/b.mp4', 4, 0),
    (b'/a/b', 8, 0), (b'/a.mp4/b', 8, 0), (b'/a/.mov', 8, 1), (b'/a/b.mp4.flac', 8, 0), (b'/a/b.', 8, 0), (b'', 8, 0),
])
def test_playback_video(fw, player, path, kind, video):
    assert fw.fn('playback_video', C.c_int, C.c_void_p)(player.song(path, kind=kind)) == video


def names(rng, seeds, count=2000):
    """Random names without a NUL, and the seeds cut short and bitten."""
    for _ in range(count):
        if rng.random() < .4:
            yield bytes(rng.randrange(1, 256) for _ in range(rng.randrange(64)))
        else:
            data = bytearray(rng.choice(seeds))
            del data[rng.randrange(len(data) + 1):]
            for _ in range(rng.randrange(3)):
                if data:
                    data[rng.randrange(len(data))] = rng.randrange(1, 256)
            yield bytes(data)


def test_playback_video_any_name(fw, player):
    video = fw.fn('playback_video', C.c_int, C.c_void_p)
    for name in names(random.Random(0x71d), [b'/m/a.mp4', b'/m/b.MKV', b'/m.mov/c.flac', b'/m/.avi']):
        tail = name.rsplit(b'.', 1)[1].lower() if b'.' in name else b''
        assert video(player.song(name)) == (b'.' in name and tail in (b'mp4', b'm4v', b'mkv', b'avi', b'mov', b'mpg'))


def test_audio_extension_any_name(fw):
    hook = fw.fn('ringnav_audio_extension', C.c_void_p, C.c_char_p, C.c_void_p)
    formats = b'.mp3.flac.wav'
    for name in names(random.Random(0xa7), [b'.m4b', b'.M4b', b'.mp3', b'.flac']):
        ext = C.create_string_buffer(name)
        got = hook(formats, ext)
        if name.lower() == b'.m4b':
            assert got == C.addressof(ext)
        else:
            assert (got is not None) == (name in formats)


def test_speed_folder_any_path(fw, player):
    folder = C.create_string_buffer(1024)
    get = fw.fn('playback__speed_folder', C.c_int, C.c_int, C.c_void_p)
    for name in names(random.Random(0x5f), [b'/a/b.flac', b'/b', b'/' + b'x' * 1022 + b'/f', b'/' + b'y' * 1021 + b'/f']):
        player.load([player.song(name)], 0)
        cut = name.rfind(b'/')
        fits = name[:1] == b'/' and cut + 1 < 1024 and cut + 1 < len(name)
        assert get(0, folder) == fits and (not fits or folder.value == name[:cut + 1])


def test_a_speed_file_any_bytes(saved, fw):
    saved.load([saved.song(b'/b/a/1.m4b')], 0)
    name = speed_files(saved, b'/b/a/', 2)
    good = saved.disk.files[name]
    rng, mode = random.Random(0x5fe), fw.var('g_playspeed_mode', C.c_ubyte)
    start = fw.fn('playback_speed_start', None)
    for _ in range(1500):
        data = bytearray(good)
        kind = rng.randrange(4)
        if kind == 0:
            data[rng.randrange(1028):] = b''
        elif kind == 1:
            data[rng.randrange(1028)] = rng.randrange(256)
        elif kind == 2:
            data[1024:] = struct.pack('<I', rng.choice((0, 3, 4, 0xffffffff, rng.getrandbits(32))))
        else:
            data[:] = rng.randbytes(1028)
        saved.disk.files[name] = bytes(data)
        mode.value = 9
        start()
        whole = len(data) == 1028 and data[1023] == 0 and bytes(data).split(b'\0')[0] == b'/b/a/'
        value = struct.unpack_from('<I', data, 1024)[0] if whole else 4
        assert mode.value == (value if value < 4 else 0)


def test_playback_video_of_a_record_with_no_path(fw):
    assert fw.fn('playback_video', C.c_int, C.c_void_p)(C.create_string_buffer(b'\0' * 0x34 + b'\x08' + b'\0' * 0x30)) == 0


def test_a_load_ends_advanced_play_and_counts(fw, player):
    load = fw.fn('ringnav_load', C.c_int, C.c_void_p, C.c_int, C.c_int)
    rows = player.load(player.albums(3))
    assert player.set(0, 3) == 1
    fw.mock('stock_load_trampoline', lambda q, at, kind, *_: 11)
    assert load(player.handle, 0, 2) == 11
    assert player.s.active == 0 and fw.var('playback_loads', C.c_uint).value == 1 and rows


def test_a_library_load_keeps_a_repeat_or_track_shuffle(fw, player):
    load = fw.fn('ringnav_load', C.c_int, C.c_void_p, C.c_int, C.c_int)
    player.load(player.albums(3))
    assert player.set(0, 1) == 1
    player.s.pend[0], player.s.npend = 1, 1
    assert load(player.handle, 0, LIBRARY) == 0
    assert player.s.active == 1 and player.s.npend == 0 and player.s.shuffle == 1


@pytest.mark.parametrize('shuffle, grouping, active', [(3, 0, 0), (4, 0, 0), (3, 1, 1)])
def test_shuffle_albums_end_with_another_queue(fw, player, shuffle, grouping, active):
    load = fw.fn('ringnav_load', C.c_int, C.c_void_p, C.c_int, C.c_int)
    player.load(player.albums(3))
    assert player.set(0, shuffle) == 1
    player.s.grouping = grouping
    load(player.handle, 0, FOLDER)
    assert player.s.active == active


def test_a_restored_load_keeps_its_type_and_its_state(fw, player, mcl):
    load = fw.fn('ringnav_load', C.c_int, C.c_void_p, C.c_int, C.c_int)
    player.load(player.albums(3))
    assert player.set(0, 1) == 1
    mcl.type, player.s.restoring = 0xf00a, 2
    seen = []
    fw.mock('stock_load_trampoline', lambda q, at, kind, *_: seen.append(kind & 0xffffffff))
    load(player.handle, 0, 5)
    assert seen == [0xf00a] and player.s.active == 1 and player.s.restoring == 0


def test_load_hands_stock_a_copy_without_the_videos(fw, player):
    load = fw.fn('ringnav_load', C.c_int, C.c_void_p, C.c_int, C.c_int)
    rows = [player.song(b'/m/1.flac'), player.song(b'/m/2.mp4'), player.song(b'/m/3.flac')]
    q, seen = player.deques.new(rows), []
    fw.mock('stock_load_trampoline', lambda dq, at, kind, *_: seen.append((list(player.deques[dq]), at)) and 0)
    load(q, 2, FOLDER)
    assert seen == [([rows[0], rows[2]], 1)] and player.deques[q] == rows


def test_the_mode_shuffle_songs_replaced_comes_back_once(fw, player, mcl):
    shuffle = fw.fn('playback_shuffle', C.c_int, C.c_int)
    unshuffle = fw.fn('playback_unshuffle', C.c_int)
    player.load(player.albums(3))
    mcl.mode = 3
    assert unshuffle() == 3
    assert shuffle(0) == 1 and player.s.shuffle == 1 and player.s.repeat == REPEAT_ALL and player.s.folder == 0
    mcl.mode = 2
    assert unshuffle() == 0 and unshuffle() == 2


def test_stock_shuffle_mode_is_not_kept_to_restore(fw, player, mcl):
    player.load(player.albums(3))
    assert fw.fn('playback_shuffle', C.c_int, C.c_int)(2) == 1
    mcl.mode = 1
    assert fw.fn('playback_unshuffle', C.c_int)() == 1


def test_load_restores_the_mode_shuffle_songs_replaced(fw, player):
    player.load(player.albums(3))
    fw.fn('playback_shuffle', C.c_int, C.c_int)(3)
    fw.fn('ringnav_load', C.c_int, C.c_void_p, C.c_int, C.c_int)(player.handle, 0, FOLDER)
    assert [c[1][:2] for c in fw.calls('config_playmode')] == [(3, 1)]


def test_a_mode_choice_ends_advanced_play_but_stock_shuffle_keeps_groups(fw, player):
    mode = fw.fn('ringnav_mode', C.c_int, C.c_int)
    player.load(player.albums(3))
    assert player.set(0, 3) == 1
    mode(2)
    assert player.s.active == 1
    mode(0)
    assert player.s.active == 0


def test_listable(fw, player, mcl):
    listable = fw.fn('playback_listable', C.c_int)
    player.load(player.albums(3))
    assert listable() == 0
    mcl.mode = 2
    assert listable() == 1 and player.s.shuffle == 1  # stock Shuffle, given an order
    player.set(0, 0)
    assert listable() == 0


@pytest.mark.parametrize('active, mode, pos, n, expected', [
    (0, 2, 0, 3, -2), (0, 2, 0, 1, -1), (0, 2, 3, 3, -1),  # stock Shuffle draws at the change
    (0, 0, 0, 3, 1), (0, 3, 2, 3, 0), (0, 3, 1, 3, 2), (0, 1, 0, 3, -1),  # Repeat All wraps
])
def test_crossfades_next_without_advanced_play(fw, player, active, mode, pos, n, expected):
    player.load([0] * n, pos)
    player.mcl.mode = mode
    assert fw.fn('playback__playback_next', C.c_int)() == expected


def test_crossfades_next_in_advanced_play(fw, player):
    plan(player, [0, 1, 2], seen=(0,))
    assert fw.fn('playback__playback_next', C.c_int)() == 1


# The back of stock Shuffle's Previous.
def test_back_returns_the_track_previous_left_once(fw, player, mcl):
    back = fw.fn('playback_back', C.c_int)
    prev = fw.fn('ringnav_prev', C.c_int)
    player.load([0] * 5, 3)
    mcl.mode = 2
    fw.mock('stock_prev_trampoline', lambda *_: setattr(mcl, 'pos', 1))
    prev()
    assert back() == 3 and back() == -1


def test_back_needs_the_track_previous_landed_on(fw, player, mcl):
    player.load([0] * 5, 3)
    mcl.mode = 2
    fw.mock('stock_prev_trampoline', lambda *_: setattr(mcl, 'pos', 1))
    fw.fn('ringnav_prev', C.c_int)()
    mcl.pos = 2
    assert fw.fn('playback_back', C.c_int)() == -1


def test_back_is_stock_shuffles_only(fw, player, mcl):
    player.load([0] * 5, 3)
    mcl.mode = 0
    fw.mock('stock_prev_trampoline', lambda *_: setattr(mcl, 'pos', 1))
    fw.fn('ringnav_prev', C.c_int)()
    assert fw.fn('playback_back', C.c_int)() == -1


# The file extension hook.
def test_audio_extension(fw):
    keep = C.create_string_buffer(b'.mp3.flac')
    ext = C.create_string_buffer(b'.M4B')
    hook = fw.fn('ringnav_audio_extension', C.c_void_p, C.c_void_p, C.c_void_p)
    assert hook(keep, ext) == C.addressof(ext)
    assert hook(keep, C.create_string_buffer(b'.flac')) == C.addressof(keep) + 4
    assert hook(keep, C.create_string_buffer(b'.mp4')) is None


# The wheel: WHEEL_POST and everything after it is stock's, so a stand-in logs each post.
class Wheel:
    def __init__(self, fw, capacity=64, used=0, full=0):
        self.fw = fw
        self.log = (C.c_int * 4096)()
        claim(O['WHEEL_POST'])
        code = b'\x48\xb8' + struct.pack('<Q', C.addressof(self.log)) + bytes.fromhex('8b08 ff00 8b5738 8954c808 8b5720 8954c80c c3')
        C.memmove(O['WHEEL_POST'], code, len(code))
        self.q = (C.c_ushort * 4)(0, used, full, capacity)
        self.loop = C.create_string_buffer(0x40)
        self.info = C.create_string_buffer(0x40)
        struct.pack_into('<Q', self.loop, O['LOOP_QUEUE'], C.addressof(self.q))
        struct.pack_into('<Q', self.info, O['WHEEL_INFO_LOOP'], C.addressof(self.loop))
        self.sample = fw.fn('wheel_sample', None, C.POINTER(C.c_int), C.c_int, C.c_void_p)
        self.post = fw.fn('playback__wheel_post', None, C.c_void_p, C.c_int)
        self.now = 100000
        fw.mock('time_now_ms', lambda *_: self.now)

    def touch(self, x, pressure=1, ret=1):
        s = (C.c_int * 6)(x, 0, pressure)
        self.sample(s, ret, self.info)
        return s[0]

    def lift(self, x=0):
        return self.touch(x, 0)

    def posts(self):
        return [tuple(self.log[2 + 2 * i:4 + 2 * i]) for i in range(self.log[0])]

    def ticks(self):
        """The keys posted: each a press then a release."""
        posts = self.posts()
        assert [event for _, event in posts] == [0x110, 0x114] * (len(posts) // 2)
        assert all(a[0] == b[0] for a, b in zip(posts[::2], posts[1::2]))
        return ['prev' if key == O['KEY_PREV'] else 'next' for key, _ in posts[::2]]


@in_fork
def test_wheel_post_presses_and_releases_the_key(fw):
    wheel = Wheel(fw)
    wheel.post(wheel.info, 172)
    assert wheel.posts() == [(172, 0x110), (172, 0x114)]


@in_fork
@pytest.mark.parametrize('head, tail, full, capacity, posts', [
    (0, 54, 0, 64, 2),   # 10 free: the headroom and the press and release
    (0, 55, 0, 64, 0),
    (60, 50, 0, 64, 2),  # wrapped: 54 used
    (60, 51, 0, 64, 0),
    (0, 0, 0, 64, 2),    # empty, as a head on its tail is unless the full flag says otherwise
    (5, 5, 1, 64, 0),
])
def test_wheel_post_keeps_the_queues_headroom(fw, head, tail, full, capacity, posts):
    wheel = Wheel(fw)
    wheel.q[:] = [head, tail, full, capacity]
    wheel.post(wheel.info, 173)
    assert len(wheel.posts()) == posts


@in_fork
def test_wheel_tick_after_28_units(fw):
    wheel = Wheel(fw)
    wheel.touch(100)
    assert wheel.touch(127) == 100 and wheel.ticks() == []
    assert wheel.touch(128) == 100 and wheel.ticks() == ['prev']


@in_fork
def test_wheel_falling_x_is_next(fw):
    wheel = Wheel(fw)
    wheel.touch(100), wheel.touch(72)
    assert wheel.ticks() == ['next']


@in_fork
def test_wheel_fast_travel_ticks_each_step_and_carries_the_rest(fw):
    wheel = Wheel(fw)
    wheel.touch(50), wheel.touch(110)
    assert wheel.ticks() == ['prev', 'prev']
    wheel.touch(133)
    assert wheel.ticks() == ['prev', 'prev']
    wheel.touch(134)
    assert wheel.ticks() == ['prev'] * 3


@in_fork
def test_wheel_reversal_starts_the_count_again(fw):
    wheel = Wheel(fw)
    wheel.touch(100), wheel.touch(120), wheel.touch(100)
    assert wheel.ticks() == []
    wheel.touch(93)
    assert wheel.ticks() == []
    wheel.touch(72)
    assert wheel.ticks() == ['next']


@in_fork
def test_wheel_crosses_its_seam(fw):
    wheel = Wheel(fw)
    wheel.touch(190), wheel.touch(18)
    assert wheel.ticks() == ['prev']
    wheel.lift(), wheel.touch(10), wheel.touch(182)
    assert wheel.ticks() == ['prev', 'next']


@in_fork
def test_wheel_lift_ends_the_touch(fw):
    wheel = Wheel(fw)
    held = fw.var('wheel_held', C.c_int)
    assert wheel.touch(100) == 100 and held.value == 1
    wheel.touch(120)
    assert wheel.lift(120) == 100 and held.value == 0
    assert wheel.lift(77) == 0  # lifted already: stock sees 0, as it sees after its own lift
    wheel.touch(140), wheel.touch(160)
    assert wheel.ticks() == []  # the carry went with the touch


@in_fork
def test_wheel_read_failures_change_nothing(fw):
    wheel = Wheel(fw)
    wheel.touch(100)
    assert wheel.touch(170, ret=0) == 170 and wheel.touch(170, ret=-1) == 170
    assert wheel.ticks() == []
    assert wheel.touch(128) == 100 and wheel.ticks() == ['prev']


@in_fork
def test_wheel_raw_counts_travel_and_posts_nothing(fw):
    wheel = Wheel(fw)
    fw.var('wheel_raw', C.c_int).value = 1
    wheel.touch(100)
    assert wheel.touch(130) == 100 and wheel.touch(90) == 100
    assert fw.var('wheel_travel', C.c_int).value == -10 and wheel.ticks() == []


@in_fork
@pytest.mark.parametrize('pct, travel', [(50, 28), (100, 14), (200, 7), (150, 10), (160, 9)])
def test_wheel_sensitivity_is_the_travel_per_tick(fw, pct, travel):
    wheel = Wheel(fw)
    fw.fn('wheel_set_pct', None, C.c_int)(pct)
    wheel.touch(50), wheel.touch(50 + travel - 1)
    assert wheel.ticks() == []
    wheel.touch(50 + travel)
    assert wheel.ticks() == ['prev']


@in_fork
def test_wheel_sensitivity_changed_mid_turn_keeps_the_carrys_share(fw):
    wheel = Wheel(fw)
    wheel.touch(100), wheel.touch(114)  # half of 28
    fw.fn('wheel_set_pct', None, C.c_int)(100)
    wheel.touch(114)
    assert wheel.ticks() == []  # no burst
    wheel.touch(120)
    assert wheel.ticks() == []
    wheel.touch(121)  # the other half of 14
    assert wheel.ticks() == ['prev']


@pytest.mark.parametrize('value, pct', [
    (b'100', 100), (b'50', 50), (b'200', 200), (b'150', 150), (b'', 50), (b'40', 50), (b'210', 50), (b'105', 50),
    (b'0100', 50), (b' 100', 50), (b'100 ', 50), (b'+100', 50), (b'abc', 50), (b'-100', 50), (b'4294967396', 50),
])
def test_wheel_sensitivity_config(fw, value, pct):
    fw.mock('toolsReadConfig', lambda path, section, key, out, default, *_: C.memmove(out, value + b'\0', len(value) + 1) and 0)
    assert fw.fn('wheel_pct', C.c_int)() == pct
    assert fw.var('playback__wheel_units', C.c_int).value == (28 * 16 * 50 + pct // 2) // pct


def test_wheel_sensitivity_is_read_once(fw):
    pct = fw.fn('wheel_pct', C.c_int)
    assert pct() == 50 and pct() == 50 and len(fw.calls('toolsReadConfig')) == 1


def test_wheel_sensitivity_any_text(fw):
    rng = random.Random(0xfee1)
    current = {}
    fw.mock('toolsReadConfig', lambda path, section, key, out, default, *_: C.memmove(out, current['v'] + b'\0', len(current['v']) + 1) and 0)
    read = fw.var('playback__wheel_read', C.c_int)
    pct = fw.fn('wheel_pct', C.c_int)
    for value in names(rng, [b'100', b'200', b'50', b'150', b'040'], 1500):
        current['v'] = value[:15]
        read.value = 0
        good = re.fullmatch(rb'[1-9]\d*', current['v']) and 50 <= int(current['v']) <= 200 and int(current['v']) % 10 == 0
        assert pct() == (int(current['v']) if good else 50)


@pytest.mark.parametrize('n, pct', [(0, 50), (49, 50), (54, 50), (55, 60), (104, 100), (105, 110), (199, 200), (200, 200), (999, 200), (-5, 50)])
def test_wheel_sensitivity_steps_in_tens(fw, n, pct):
    fw.fn('wheel_set_pct', None, C.c_int)(n)
    assert fw.fn('wheel_pct', C.c_int)() == pct


def test_wheel_units_for_a_saved_scale(fw):
    fw.fn('wheel_set_pct', None, C.c_int)(160)
    assert fw.var('playback__wheel_units', C.c_int).value == 140  # 8.75 units, in sixteenths


# The volume guard, screen off.
def dark(fw):
    fw.var('wheel_guard', C.c_int).value = 1
    wheel = Wheel(fw)
    wheel.touch(100)
    return wheel


def guard(fw):
    return fw.var('playback__wh_guard', C.c_int).value


@in_fork
def test_guard_holds_a_brush(fw):
    wheel = dark(fw)
    assert wheel.touch(199) == 100 and wheel.ticks() == []  # 99 units


@in_fork
def test_guard_passes_one_tick_for_28_units_past_it(fw):
    wheel = dark(fw)
    wheel.touch(190)  # 90
    wheel.touch(38)   # 48 more, the seam crossed: 138, 38 past
    assert wheel.ticks() == ['prev'] and guard(fw) == 1600


@in_fork
def test_guard_open_ticks_as_lit_reversals_and_lifts_too(fw):
    wheel = dark(fw)
    wheel.touch(190), wheel.touch(28)  # 128 units across the seam: 28 past
    assert wheel.ticks() == ['prev']
    wheel.touch(190)  # back across it: a reversal, 38 units
    assert wheel.ticks() == ['prev', 'next']
    wheel.lift()
    wheel.touch(100), wheel.touch(128)
    assert wheel.ticks() == ['prev', 'next', 'prev']


@in_fork
def test_guard_reversal_arms_it_again(fw):
    wheel = dark(fw)
    wheel.touch(180)  # 80
    wheel.touch(170)  # back 10: starts again, the other way
    assert guard(fw) == -160
    wheel.touch(110), wheel.touch(60)  # 110 down in all, 10 past
    assert guard(fw) == 1600 and wheel.ticks() == []


@in_fork
def test_guard_lift_before_it_opens_arms_it_again(fw):
    wheel = dark(fw)
    wheel.touch(180)
    wheel.lift()
    assert guard(fw) == 0


@in_fork
def test_guard_stillness_arms_it_again(fw):
    wheel = dark(fw)
    wheel.touch(190), wheel.touch(18)
    assert guard(fw) == 1600
    wheel.now += 1499
    wheel.touch(18)
    assert guard(fw) == 1600
    wheel.now += 1500
    wheel.touch(18)
    assert guard(fw) == 0


@in_fork
def test_guard_stays_out_of_a_lit_screen(fw):
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    wheel = dark(fw)
    wheel.touch(128)
    assert wheel.ticks() == ['prev'] and guard(fw) == 0


@in_fork
def test_guard_off_by_default_ticks_dark(fw):
    wheel = Wheel(fw)
    wheel.touch(100), wheel.touch(128)
    assert wheel.ticks() == ['prev']


def test_guard_reset(fw):
    fw.var('playback__wh_guard', C.c_int).value = 77
    fw.fn('wheel_guard_reset', None)()
    assert guard(fw) == 0


# The snapshot.
def snapshot(data):
    head = struct.unpack_from(HEADER, data)
    names = 'magic version size checksum n pos elapsed shuffle repeat folder cursor hn npend type'.split()
    h = dict(zip(names, head))
    words = h['n'] * 3 + h['hn'] + h['npend']
    v = struct.unpack_from(f'<{words}I', data, 56)
    n = h['n']
    h['order'], h['cycle'], h['seen'] = list(v[:n]), list(v[n:2 * n]), list(v[2 * n:3 * n])
    h['history'], h['pending'] = list(v[3 * n:3 * n + h['hn']]), list(v[3 * n + h['hn']:])
    at, h['songs'] = 56 + words * 4, []
    for _ in range(n):
        size, start, end = struct.unpack_from('<3I', data, at)
        path = data[at + 12:at + 12 + size]
        assert path[-1] == 0
        h['songs'].append((path[:-1], start, end))
        at += 12 + size
    assert at == len(data) == h['size']
    return h


def sealed(data):
    """data with its checksum right, as a torn write's never is."""
    data = bytearray(data)
    data[12:16] = b'\0' * 4
    data[12:16] = struct.pack('<I', fnv1a(data))
    return bytes(data)


class Saved(Player):
    def __init__(self, fw, mcl):
        super().__init__(fw, mcl)
        self.disk = Files(fw)
        self.save = fw.fn('playback_save', None)
        self.memory = fw.fn('ringnav_memory', C.c_int, C.c_void_p)
        self.sec = 0
        fw.mock('mclGetPlayTime', lambda sec, total, *_: C.c_int.from_address(sec).__setattr__('value', self.sec))
        fw.mock('stock_memory_trampoline', lambda *_: -7)
        fw.mock('mclLoadPlayList', self.stock_load)
        fw.var('g_memory_play', C.c_ubyte).value = 2
        self.library = self.deques.new()  # the library's rows, which getAllMusic fills staged()'s deque with
        fw.mock('getAllMusic', lambda *_: self.fill(self.library))
        self.out = self.deques.new()

    def fill(self, rows):
        """A stock query's rows into the deque staged() left in tools_pdeq_directory."""
        self.deques.assign(self.fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value, rows)
        return len(self.deques[rows])

    def stock_load(self, q, at, kind, *_):
        self.deques.assign(self.handle, q)
        self.mcl.pos, self.mcl.type = C.c_int(at).value, C.c_int(kind).value

    def queue_file(self):
        return self.disk.files[QUEUE_FILE]

    def forget(self):
        """A reboot: nothing but the files stays."""
        self.clear()
        self.deques[self.handle].clear()
        self.mcl.pos = 0
        self.s.dirty = 0


@pytest.fixture
def saved(fw, mcl):
    return Saved(fw, mcl)


def full_queue(saved, shuffle=1, repeat=REPEAT_ALL):
    rows = saved.load([saved.song(b'/m/%c/%02d.flac' % (65 + i % 3, i), b'%c' % (65 + i % 3), track=i, cue=(i, i * 2))
                       for i in range(9)], 4)
    saved.mcl.type = LIBRARY
    saved.deques[saved.library][:] = rows
    saved.rng(0xbeef)
    assert saved.set(0, shuffle) == 1 and saved.set(1, repeat) == 1
    saved.run(2)
    saved.s.pend[:2], saved.s.npend = saved.up_next()[-2:], 2
    return rows


def test_a_snapshot_holds_the_whole_play_state(saved):
    rows = full_queue(saved)
    saved.sec = 61
    saved.s.dirty = 1
    saved.save()
    h = snapshot(saved.queue_file())
    assert (h['magic'], h['version'], h['n'], h['pos'], h['elapsed']) == (MAGIC, 3, 9, saved.mcl.pos, 61)
    assert (h['shuffle'], h['repeat'], h['folder'], h['cursor'], h['type']) == (1, REPEAT_ALL, 0, saved.s.cursor, LIBRARY)
    assert h['order'] == saved.order() and h['cycle'] == saved.cycle()
    assert {i for i, v in enumerate(h['seen']) if v} == saved.seen()
    assert h['history'] == saved.history() and h['pending'] == saved.pending()
    assert h['songs'] == [(b'/m/%c/%02d.flac' % (65 + i % 3, i), i, i * 2) for i in range(9)]
    assert sealed(saved.queue_file()) == saved.queue_file() and rows


def test_a_resume_not_yet_playing_keeps_its_second(saved):
    full_queue(saved)
    saved.sec, saved.s.dirty = 33, 1
    saved.save()
    pos = saved.mcl.pos
    assert restore(saved) == pos
    saved.sec, saved.s.dirty = 0, 1
    saved.save()
    assert snapshot(saved.queue_file())['elapsed'] == 33
    saved.sec, saved.s.dirty = 4, 1
    saved.save()
    saved.sec, saved.s.dirty = 0, 1
    saved.save()
    assert snapshot(saved.queue_file())['elapsed'] == 0  # it played: 0 now means 0


def test_a_snapshots_elapsed_second_goes_to_its_own_file(saved):
    full_queue(saved)
    saved.sec, saved.s.dirty = 10, 1
    saved.save()
    body = saved.queue_file()
    saved.sec = 11
    saved.save()
    assert saved.queue_file() == body
    check = struct.unpack('<4I', saved.disk.files[CHECKPOINT])
    assert check[1:] == (struct.unpack_from('<I', body, 12)[0], saved.mcl.pos, 11)
    assert check[0] == fnv1a(saved.disk.files[CHECKPOINT][4:])


def test_nothing_changed_writes_nothing(saved):
    full_queue(saved)
    saved.s.dirty = 1
    saved.save()
    saved.disk.files.clear()
    saved.save()
    assert not saved.disk.files


def test_a_failed_write_stays_due(saved):
    full_queue(saved)
    saved.s.dirty = 1
    saved.disk.fail_rename = True
    saved.save()
    assert saved.s.save_failed == 1 and saved.s.dirty == 1
    saved.disk.fail_rename = False
    saved.save()
    assert saved.s.save_failed == 0 and saved.s.dirty == 0 and QUEUE_FILE in saved.disk.files


def test_a_queue_past_the_limit_drops_its_snapshot(saved):
    full_queue(saved)
    saved.s.dirty, saved.disk.files[QUEUE_FILE] = 1, b'old'
    saved.s.n = 1000
    saved.save()
    assert QUEUE_FILE not in saved.disk.files and saved.s.dirty == 0
    saved.s.n = 9


def test_a_position_past_the_queue_keeps_the_last_snapshot(saved):
    full_queue(saved)
    saved.s.dirty, saved.disk.files[QUEUE_FILE] = 1, b'old'
    saved.mcl.pos = 9
    saved.save()
    assert saved.disk.files[QUEUE_FILE] == b'old'


@pytest.mark.parametrize('path, kept', [(b'/m/' + b'x' * 1020, True), (b'/m/' + b'x' * 1021, False), (b'', False)])
def test_a_snapshot_path_is_at_most_1023(saved, path, kept):
    rows = full_queue(saved)
    rows[3] = saved.song(path)
    saved.deques[saved.handle][:] = rows
    saved.s.dirty = 1
    saved.disk.files.clear()
    saved.save()
    assert (QUEUE_FILE in saved.disk.files) == kept


def plain_song(saved, path, k):
    """queue_hash walks REC_NAME and REC_PATH, a word apart and each read 8 bytes wide here: a path at an
    address whose low half is zero leaves both reads good."""
    at = 0x200000000000 + (k << 32)
    assert libc.mmap(at, 0x1000, 3, 0x32, -1, 0) == at  # MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED
    C.memmove(at, path + b'\0', len(path) + 1)
    record = saved.song(b'')
    struct.pack_into('<Q', (C.c_char * 0x60).from_address(record), O['REC_PATH'], at)
    return record


def plain_queue(saved):
    rows = saved.load([plain_song(saved, b'/m/%d.flac' % i, i + 1) for i in range(3)], 1)
    saved.mcl.type = FOLDER
    saved.deques[saved.library][:] = rows
    saved.s.dirty = 1
    return rows


def test_a_plain_queue_saves_as_version_4(saved):
    plain_queue(saved)
    saved.save()
    h = snapshot(saved.queue_file())
    assert (h['version'], h['n'], h['pos'], h['cursor'], h['type']) == (4, 3, 1, 1, FOLDER)
    assert (h['shuffle'], h['repeat'], h['folder'], h['hn'], h['npend']) == (0, 2, 0, 0, 0)
    assert h['order'] == h['cycle'] == [0, 1, 2] and h['seen'] == [0, 0, 0]


def test_stocks_own_edit_of_a_plain_queue_is_seen(saved):
    rows = plain_queue(saved)
    saved.save()
    body = saved.queue_file()
    saved.deques[saved.handle][:] = rows[:2]
    saved.save()
    assert saved.queue_file() != body and snapshot(saved.queue_file())['n'] == 2


def test_a_plain_queue_restores_without_advanced_play(saved):
    rows = plain_queue(saved)
    saved.save()
    assert restore(saved) == 1
    assert saved.s.active == 0 and saved.deques[saved.handle] == rows and saved.deques[saved.out] == rows


def test_a_plain_queue_is_kept_only_for_memory_playback(saved):
    plain_queue(saved)
    saved.fw.var('g_memory_play', C.c_ubyte).value = 0
    saved.save()
    assert QUEUE_FILE not in saved.disk.files and saved.s.dirty == 0


def test_a_card_app_stream_is_not_kept(saved):
    plain_queue(saved)
    saved.mcl.type = 2
    saved.save()
    assert QUEUE_FILE not in saved.disk.files


def test_airplay_is_not_kept(saved):
    plain_queue(saved)
    saved.fw.mock('airplayGetFlag', lambda *_: 2)
    saved.save()
    assert QUEUE_FILE not in saved.disk.files


def test_boots_empty_queue_keeps_the_last_snapshot(saved):
    plain_queue(saved)
    saved.deques[saved.handle].clear()
    saved.disk.files[QUEUE_FILE] = b'old'
    saved.save()
    assert saved.disk.files[QUEUE_FILE] == b'old' and saved.s.dirty == 1


def restore(saved):
    data = dict(saved.disk.files)
    saved.forget()
    saved.disk.files.clear()
    saved.disk.files.update(data)
    return saved.memory(saved.out)


def test_a_snapshot_restores_what_it_saved(saved):
    rows = full_queue(saved)
    saved.sec, saved.s.dirty = 33, 1
    saved.save()
    before = (saved.order(), saved.cycle(), saved.seen(), saved.history(), saved.pending(), saved.s.cursor)
    pos, shuffle, repeat = saved.mcl.pos, saved.s.shuffle, saved.s.repeat
    assert restore(saved) == pos
    assert saved.deques[saved.handle] == rows and saved.deques[saved.out] == rows
    assert (saved.s.active, saved.s.shuffle, saved.s.repeat) == (1, shuffle, repeat)
    assert (saved.order(), saved.cycle(), saved.seen(), saved.history(), saved.pending(), saved.s.cursor) == before
    assert saved.fw.calls('mclSetStartSeekTime')[-1][1][0] == 33


def test_restored_play_carries_on_as_it_would_have(saved):
    full_queue(saved)
    saved.s.dirty = 1
    saved.save()
    expected = saved.up_next()
    pos = saved.mcl.pos
    assert restore(saved) == pos
    assert saved.up_next() == expected


def test_the_checkpoint_elapsed_wins_when_it_matches(saved):
    full_queue(saved)
    saved.sec, saved.s.dirty = 10, 1
    saved.save()
    saved.sec = 90
    saved.save()
    restore(saved)
    assert saved.fw.calls('mclSetStartSeekTime')[-1][1][0] == 90


def test_a_stale_checkpoint_is_ignored(saved):
    full_queue(saved)
    saved.sec, saved.s.dirty = 10, 1
    saved.save()
    check = bytearray(saved.disk.files[CHECKPOINT])
    check[12:16] = struct.pack('<I', 500)  # elapsed changed, its checksum not
    saved.disk.files[CHECKPOINT] = bytes(check)
    restore(saved)
    assert saved.fw.calls('mclSetStartSeekTime')[-1][1][0] == 10


def test_memory_playback_off_seeks_nowhere(saved):
    full_queue(saved)
    saved.sec, saved.s.dirty = 10, 1
    saved.save()
    saved.fw.var('g_memory_play', C.c_ubyte).value = 1
    restore(saved)
    assert saved.fw.calls('mclSetStartSeekTime')[-1][1][0] == 0


def test_no_memory_playback_is_stocks(saved):
    saved.fw.var('g_memory_play', C.c_ubyte).value = 0
    assert saved.memory(saved.out) == -7 and not saved.fw.calls('fw_fopen')


def test_no_snapshot_is_stocks(saved):
    assert saved.memory(saved.out) == -7


def test_the_same_song_twice_restores_twice(saved):
    a, b = saved.song(b'/m/a.flac'), saved.song(b'/m/b.flac')
    saved.load([a, b, a], 0)
    saved.mcl.type = LIBRARY
    saved.deques[saved.library][:] = [a, b]
    assert saved.set(1, ONE_PASS) == 1
    saved.s.dirty = 1
    saved.save()
    assert restore(saved) == 0 and saved.deques[saved.handle] == [a, b, a]


def test_cue_tracks_of_one_file_restore_apart(saved):
    rows = [saved.song(b'/m/image.flac', cue=(i * 60, (i + 1) * 60)) for i in range(3)]
    saved.load(rows, 2)
    saved.mcl.type = LIBRARY
    saved.deques[saved.library][:] = rows[::-1]
    assert saved.set(1, ONE_PASS) == 1
    saved.s.dirty = 1
    saved.save()
    assert restore(saved) == 2 and saved.deques[saved.handle] == rows


def test_a_missing_file_drops_its_song_and_the_rest_play(saved):
    rows = full_queue(saved)
    saved.s.dirty = 1
    saved.save()
    pos = saved.mcl.pos
    gone = (pos + 1) % 9
    saved.fw.mock('fw_access', lambda path, *_: b'/%02d.' % gone in C.string_at(path))
    at = restore(saved)
    assert len(saved.deques[saved.handle]) == 8 and rows[gone] not in saved.deques[saved.handle]
    assert saved.deques[saved.handle][at] == rows[pos] and sorted(saved.order()) == list(range(8))


def test_a_lost_current_track_starts_the_next_from_the_top(saved):
    rows = full_queue(saved)
    saved.sec, saved.s.dirty = 44, 1
    saved.save()
    order, cursor, pos = saved.order(), saved.s.cursor, saved.mcl.pos
    saved.fw.mock('fw_access', lambda path, *_: b'/%02d.' % pos in C.string_at(path))
    at = restore(saved)
    assert saved.deques[saved.handle][at] == rows[order[cursor + 1]]
    assert saved.fw.calls('mclSetStartSeekTime')[-1][1][0] == 0


def test_all_lost_is_stocks(saved):
    full_queue(saved)
    saved.s.dirty = 1
    saved.save()
    saved.fw.mock('fw_access', lambda *_: -1)
    assert restore(saved) == -7


def test_a_song_off_the_library_comes_from_its_folder(saved):
    rows = full_queue(saved)
    saved.s.dirty = 1
    saved.save()
    pos = saved.mcl.pos
    saved.deques[saved.library][:] = rows[:8]
    folder = saved.deques.new(rows)
    saved.fw.mock('toolsLoadDirectory', lambda path, *_: saved.fill(folder))
    assert restore(saved) == pos
    assert rows[8] in saved.deques[saved.handle]


def snapshot_words(saved):
    full_queue(saved)
    saved.s.dirty = 1
    saved.save()
    return bytearray(saved.queue_file())


@pytest.mark.parametrize('field, value', [
    (0, 0), (1, 1), (1, 5), (4, 0), (4, 65537), (5, 9), (10, 9), (11, 4097), (12, 65), (7, 5), (8, 6), (9, 2), (6, 0x80000000),
    (13, 0), (13, 0x1000), (13, 0x1ffff), (2, 0), (2, 0xffffffff),
])
def test_a_field_out_of_range_is_refused_even_sealed(saved, field, value):
    data = snapshot_words(saved)
    struct.pack_into('<I', data, field * 4, value)
    saved.disk.files[QUEUE_FILE] = sealed(data)
    assert restore(saved) == -7


def test_a_bad_checksum_is_refused(saved):
    data = snapshot_words(saved)
    data[100] ^= 1
    saved.disk.files[QUEUE_FILE] = bytes(data)
    assert restore(saved) == -7


def test_a_short_file_and_a_long_file_are_refused(saved):
    data = bytes(snapshot_words(saved))
    for body in (data[:-1], data[:55], data[:47], b'', data + b'\0'):
        saved.disk.files[QUEUE_FILE] = body
        assert restore(saved) == -7


@pytest.mark.parametrize('section, where', [('order', 0), ('cycle', 9), ('seen', 18), ('history', 27)])
def test_an_index_out_of_range_is_refused(saved, section, where):
    data = snapshot_words(saved)
    struct.pack_into('<I', data, 56 + where * 4, 2 if section == 'seen' else 9)
    saved.disk.files[QUEUE_FILE] = sealed(data)
    assert restore(saved) == -7


def test_an_order_that_is_no_permutation_is_refused(saved):
    data = snapshot_words(saved)
    struct.pack_into('<I', data, 56 + 4, struct.unpack_from('<I', data, 56)[0])
    saved.disk.files[QUEUE_FILE] = sealed(data)
    assert restore(saved) == -7


def test_a_cursor_off_the_current_track_is_refused(saved):
    data = snapshot_words(saved)
    cursor = struct.unpack_from('<I', data, 10 * 4)[0]
    struct.pack_into('<I', data, 10 * 4, (cursor + 1) % 9)
    saved.disk.files[QUEUE_FILE] = sealed(data)
    assert restore(saved) == -7


def version2(data):
    """data's version 3 snapshot as version 2 wrote it: no pending list, its run in the header."""
    h = snapshot(bytes(data))
    words = list(struct.unpack_from('<12I', data))
    n, hn = h['n'], h['hn']
    table = bytes(data[56 + (3 * n + hn + h['npend']) * 4:])
    indices = bytes(data[56:56 + (3 * n + hn) * 4])
    size = 60 + len(indices) + len(table)
    words[1], words[2] = 2, size
    return words, indices, table


def test_version_2_restores_its_run_as_the_pending_list(saved):
    data = snapshot_words(saved)
    rows = list(saved.deques[saved.handle])
    words, indices, table = version2(data)
    head = struct.pack('<12I', *words) + struct.pack('<3I', 2, 4, LIBRARY)
    saved.disk.files[QUEUE_FILE] = sealed(head + indices + table)
    pos = saved.mcl.pos
    assert restore(saved) == pos and saved.pending() == [1, 2, 3] and saved.deques[saved.handle] == rows


@pytest.mark.parametrize('run, end', [(10, 10), (0, 10), (5, 3)])
def test_version_2_run_out_of_range_is_refused(saved, run, end):
    data = snapshot_words(saved)
    words, indices, table = version2(data)
    head = struct.pack('<12I', *words) + struct.pack('<3I', run, end, LIBRARY)
    saved.disk.files[QUEUE_FILE] = sealed(head + indices + table)
    assert restore(saved) == (-7 if run > end or run > 9 or end > 9 else saved.mcl.pos)


def mutated(rng, base):
    data = bytearray(base)
    for _ in range(rng.randrange(1, 5)):
        at = rng.randrange(len(data))
        kind = rng.randrange(4)
        if kind == 0:
            data[at] = rng.randrange(256)
        elif kind == 1 and at + 4 <= len(data):
            struct.pack_into('<I', data, at & ~3, rng.choice((0, 1, 2, 8, 9, 10, 64, 65, 4096, 65536, 0x7fffffff, 0xffffffff)))
        elif kind == 2 and at < 56:
            struct.pack_into('<I', data, at & ~3, rng.randrange(20))
        elif kind == 3:
            del data[at:at + rng.randrange(1, 8)]
    return bytes(data)


def test_a_mangled_sealed_snapshot_never_breaks_the_restore(saved):
    base = bytes(snapshot_words(saved))
    rng = random.Random(0x5a7ed)
    restored = 0
    for i in range(2500):
        data = mutated(rng, base)
        if rng.random() < .85 and len(data) >= 16:
            head = bytearray(data)
            struct.pack_into('<I', head, 8, len(head))
            data = sealed(head) if rng.random() < .5 else sealed(data)
        saved.disk.files.clear()
        saved.disk.files[QUEUE_FILE] = data
        got = restore(saved)
        if got == -7:
            continue
        restored += 1
        n = len(saved.deques[saved.handle])
        assert 0 <= got < n
        if saved.s.active:  # a version 4 has no play state to check
            assert saved.s.n == n and sorted(saved.order()) == sorted(saved.cycle()) == list(range(n))
            assert saved.order()[saved.s.cursor] == got and all(x < n for x in saved.history() + saved.pending())
    assert restored > 50


def test_random_bytes_are_refused(saved):
    rng = random.Random(0xf11e)
    for i in range(1500):
        saved.disk.files.clear()
        saved.disk.files[QUEUE_FILE] = rng.randbytes(rng.randrange(200))
        assert restore(saved) == -7


def test_a_mangled_checkpoint_never_breaks_the_restore(saved):
    base = bytes(snapshot_words(saved))
    rng = random.Random(0xc4ec)
    for i in range(500):
        saved.disk.files[QUEUE_FILE] = base
        saved.disk.files[CHECKPOINT] = rng.randbytes(rng.choice((0, 4, 15, 16, 17)))
        assert restore(saved) >= 0


# The save's schedule, once a UI pass.
class Polled(Saved):
    def __init__(self, fw, mcl):
        super().__init__(fw, mcl)
        self.now, self.status = 100000, 2
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        self.poll = fw.fn('playback_poll', None)
        full_queue(self)
        self.sec, self.s.dirty = 10, 1
        self.poll()
        self.wrote()

    def wrote(self):
        """Whether a file changed since the last ask."""
        now = dict(self.disk.files)
        changed = now != getattr(self, 'last', None)
        self.last = now
        return changed

    def after(self, ms, sec=None):
        self.now += ms
        self.sec = self.sec + ms // 1000 if sec is None else sec
        self.poll()
        return self.wrote()


@pytest.fixture
def polled(fw, mcl):
    return Polled(fw, mcl)


def test_a_change_saves_on_the_next_pass(polled):
    assert QUEUE_FILE in polled.disk.files
    polled.s.dirty = 1
    assert polled.after(1, sec=11)


def test_a_track_playing_on_saves_every_30_s(polled):
    assert [polled.after(6000) for _ in range(4)] == [False] * 4
    assert polled.after(6000)
    assert not polled.after(6000)


def test_a_seek_goes_within_5_s(polled):
    assert not polled.after(1000, sec=200)
    assert polled.after(5000, sec=205)


def test_a_pause_goes_within_5_s(polled):
    polled.status = 3
    assert polled.after(6000)


def test_a_failed_write_retries_after_30_s(polled):
    polled.s.dirty, polled.disk.fail_rename = 1, True
    polled.after(1)
    assert polled.s.save_failed == 1
    polled.disk.fail_rename = False
    assert not polled.after(29000) and polled.s.save_failed == 1
    assert polled.after(1100) and polled.s.save_failed == 0


# Odds and ends of the player's day.
def test_resumed_is_the_snapshots_track_once(saved):
    resumed = saved.fw.fn('playback_resumed', C.c_int, C.c_void_p)
    full_queue(saved)
    saved.sec, saved.s.dirty = 33, 1  # a place to resume: a song at 0 s just starts
    saved.save()
    pos = saved.mcl.pos
    assert restore(saved) == pos
    record = saved.deques[saved.handle][pos]
    assert resumed(record) == 1 and resumed(record) == 0


def test_resumed_refuses_another_track(saved):
    resumed = saved.fw.fn('playback_resumed', C.c_int, C.c_void_p)
    rows = full_queue(saved)
    saved.s.dirty = 1
    saved.save()
    pos = saved.mcl.pos
    assert restore(saved) == pos
    assert resumed(rows[(pos + 1) % 9]) == 0


def test_a_track_with_no_queue_fits(fw, mcl):
    assert fw.fn('playback_start_fits', C.c_int)() == 1


def speed_files(saved, folder, mode):
    record = struct.pack('<1024sI', folder, mode)
    name = '/mnt/data/ringnav-speed-%08x' % (fnv1a(folder) * FNV_PRIME & 0xffffffff)
    saved.disk.files[name] = record
    return name


def test_a_folder_keeps_its_speed(saved, fw):
    saved.load([saved.song(b'/books/a/1.m4b'), saved.song(b'/books/b/1.m4b')], 0)
    mode = fw.var('g_playspeed_mode', C.c_ubyte)
    start = fw.fn('playback_speed_start', None)
    speed_files(saved, b'/books/a/', 2)
    start()
    assert mode.value == 2
    saved.mcl.pos = 1
    start()
    assert mode.value == 0  # another folder's file


@pytest.mark.parametrize('stored, value', [(4, 0), (3, 3)])
def test_a_saved_speed_is_validated(saved, fw, stored, value):
    saved.load([saved.song(b'/books/a/1.m4b')], 0)
    speed_files(saved, b'/books/a/', stored)
    fw.fn('playback_speed_start', None)()
    assert fw.var('g_playspeed_mode', C.c_ubyte).value == value


def test_a_hash_collision_has_another_folder_in_its_file(saved, fw):
    saved.load([saved.song(b'/books/a/1.m4b')], 0)
    name = speed_files(saved, b'/books/a/', 2)
    saved.disk.files[name] = saved.disk.files[name].replace(b'/books/a/', b'/books/z/')
    fw.fn('playback_speed_start', None)()
    assert fw.var('g_playspeed_mode', C.c_ubyte).value == 0


def test_a_speed_choice_saves_and_sends(saved, fw):
    speed = fw.fn('ringnav_speed', C.c_int, C.c_int)
    saved.load([saved.song(b'/books/a/1.m4b')], 0)
    fw.mock('stock_speed_trampoline', lambda mode, *_: 30 + mode)
    assert speed(3) == 33
    assert fw.var('g_playspeed_mode', C.c_ubyte).value == 3 and saved.mcl.prepos == -1
    name = '/mnt/data/ringnav-speed-%08x' % (fnv1a(b'/books/a/') * FNV_PRIME & 0xffffffff)
    assert struct.unpack('<1024sI', saved.disk.files[name])[1] == 3


def test_a_speed_for_a_stream_stays_with_the_track(saved, fw):
    speed = fw.fn('ringnav_speed', C.c_int, C.c_int)
    saved.load([saved.song(b'/books/a/1.m4b', kind=4)], 0)
    fw.mock('stock_speed_trampoline', lambda mode, *_: mode)
    assert speed(2) == 2 and fw.var('g_playspeed_mode', C.c_ubyte).value == 2 and not saved.disk.files


def test_a_failed_save_keeps_the_folders_speed(saved, fw):
    speed = fw.fn('ringnav_speed', C.c_int, C.c_int)
    saved.load([saved.song(b'/books/a/1.m4b')], 0)
    saved.disk.fail_rename = True
    speed(2)
    assert fw.var('g_playspeed_mode', C.c_ubyte).value == 0
    assert fw.calls('stock_speed_trampoline')[-1][1][0] == 0 and not saved.disk.files


@pytest.mark.parametrize('path, ok', [
    (b'/a/b.flac', 1), (b'relative.flac', 0), (b'/a/', 0), (b'/' + b'x' * 1021 + b'/f', 1), (b'/' + b'x' * 1022 + b'/f', 0),
])
def test_speed_folder_bounds(fw, player, path, ok):
    player.load([player.song(path)], 0)
    folder = C.create_string_buffer(1024)
    assert fw.fn('playback__speed_folder', C.c_int, C.c_int, C.c_void_p)(0, folder) == ok


@pytest.mark.parametrize('at', [-1, 1])
def test_speed_folder_of_no_track(fw, player, at):
    player.load([player.song(b'/a/b.flac')], 0)
    assert fw.fn('playback__speed_folder', C.c_int, C.c_int, C.c_void_p)(at, C.create_string_buffer(1024)) == 0


def test_manual_change_in_advanced_play_is_repeat_all_for_stock(fw, player, mcl):
    player.load(player.albums(3))
    assert player.set(0, 1) == 1
    mcl.mode = 1
    seen = []
    fw.mock('stock_change_trampoline', lambda next_, *_: seen.append(mcl.mode) or 6)
    assert fw.fn('ringnav_change', C.c_int, C.c_int)(1) == 6
    assert seen == [3] and mcl.mode == 1


def test_manual_next_at_a_library_lists_end_stops(fw, player, mcl):
    player.load(player.albums(2), 1)
    mcl.type = LIBRARY
    assert fw.fn('ringnav_change', C.c_int, C.c_int)(1) == 0
    assert len(fw.calls('mclStop')) == 1 and not fw.calls('stock_change_trampoline')


def test_boot_restores_with_its_mode_unsaved(fw, player):
    fw.mock('config_playmode', lambda *_: player.s.restoring + 40)
    assert fw.fn('ringnav_bootmode', C.c_int, C.c_int, C.c_int)(3, 0) == 41
    assert player.s.restoring == 0


def test_preload_in_advanced_play_opens_the_successor(fw, player, mcl):
    plan(player, [0, 1, 2], seen=(0,))
    fw.mock('mcl_open_preload', lambda *_: 4)
    assert fw.fn('ringnav_preload', C.c_int)() == 4 and mcl.prepos == 1


def test_preload_at_the_end_opens_nothing(fw, player, mcl):
    plan(player, [0, 1], cursor=1, seen=(0, 1))
    assert fw.fn('ringnav_preload', C.c_int)() == 1 and not fw.calls('mcl_open_preload')


def mp4(samples):
    stsz = struct.pack('>I4s', 20, b'stsz') + struct.pack('>3I', 0, 0, samples)
    return struct.pack('>I4s', 8 + len(stsz), b'moov') + stsz


def test_a_track_too_long_for_ram_is_refused_and_toasted_once(saved, fw):
    saved.load([saved.song(b'/b/long.m4b')], 0)
    saved.disk.files['/b/long.m4b'] = mp4(1 << 26)
    saved.disk.files['/proc/meminfo'] = b'MemTotal: 60000 kB\nMemAvailable: 40000 kB\n'
    assert fw.fn('playback_start_fits', C.c_int)() == 0
    flag = fw.var('playback__too_long', C.c_int)
    assert flag.value == 1
    fw.fn('playback_poll', None)()
    assert flag.value == 0


def test_a_track_that_fits_is_not_refused(saved, fw):
    saved.load([saved.song(b'/b/ok.m4b')], 0)
    saved.disk.files['/b/ok.m4b'] = mp4(100)
    saved.disk.files['/proc/meminfo'] = b'MemAvailable: 40000 kB\n'
    assert fw.fn('playback_start_fits', C.c_int)() == 1


def test_a_preload_too_long_is_not_opened(saved, fw, mcl):
    saved.load([saved.song(b'/b/a.flac'), saved.song(b'/b/long.m4b')], 0)
    saved.disk.files['/b/long.m4b'] = mp4(1 << 26)
    saved.disk.files['/proc/meminfo'] = b'MemAvailable: 40000 kB\n'
    mcl.prepos = 1
    assert fw.fn('ringnav_open_preload', C.c_int)() == 1 and mcl.preload == -1


def test_shuffle_groups_starts_at_an_albums_first_track(saved, fw):
    rows = saved.albums(3, 3)
    saved.deques[saved.handle][:] = rows
    all_ = saved.deques.new(rows)
    seen = []
    def to_page(name, context, *_):
        struct_ = C.string_at(context, 16)
        seen.append((C.string_at(name), struct.unpack_from('<Q', struct_)[0], struct.unpack_from('<i', struct_, 8)[0], saved.s.grouping))
    fw.mock('navigator_to_with_context', to_page)
    assert fw.fn('playback_groups', C.c_int, C.c_void_p, C.c_int)(all_, 0) == 1
    assert seen[0][0] == b'playing_page' and seen[0][1] == all_ and seen[0][3] == 1 and saved.s.grouping == 0
    assert saved.track(seen[0][2]) == 1 and saved.s.shuffle == 3 and saved.s.active == 1


def test_an_empty_list_has_no_groups(fw, player):
    assert fw.fn('playback_groups', C.c_int, C.c_void_p, C.c_int)(player.deques.new(), 0) == 0 and not fw.calls('mclLoadPlayList')


def test_back_shifts_with_an_insert_before_it(fw, player, mcl):
    player.load(player.albums(6), 1)
    C.c_uint64.from_address(O['MCL_POOL']).value = player.deques.new()
    fw.var('playback__back_to', C.c_int).value = 5  # index 4, + 1
    fw.fn('playback_queue_insert', C.c_uint, C.c_void_p, C.c_void_p, C.c_int)(player.handle, player.deques.new([player.song(b'/n')]), 1)
    assert fw.var('playback__back_to', C.c_int).value == 6
    C.c_uint64.from_address(O['MCL_POOL']).value = player.deques.new()
    fw.var('playback__back_to', C.c_int).value = 2  # index 1, before the insert at 2
    fw.fn('playback_queue_insert', C.c_uint, C.c_void_p, C.c_void_p, C.c_int)(player.handle, player.deques.new([player.song(b'/m')]), 1)
    assert fw.var('playback__back_to', C.c_int).value == 2


def test_a_preload_opens_only_at_the_same_speed(saved, fw, mcl):
    preload = fw.fn('ringnav_open_preload', C.c_int)
    saved.load([saved.song(b'/books/a/1.flac'), saved.song(b'/books/b/1.flac')], 0)
    mcl.prepos = 1
    fw.mock('stock_open_preload_trampoline', lambda *_: 7)
    speed_files(saved, b'/books/b/', 2)
    assert preload() == 1 and mcl.preload == -1
    fw.var('g_playspeed_mode', C.c_ubyte).value = 2
    assert preload() == 7


@pytest.mark.parametrize('kind, status, saved_call', [(0, 1, 0), (0, 2, 1), (1, 3, 1)])
def test_a_reboot_saves_unless_stopped(fw, kind, status, saved_call):
    fw.mock('mclGetPlayStatus', lambda *_: status)
    fw.mock('stock_reboot_trampoline', lambda *_: 9)
    fw.mock('stock_checkfw_trampoline', lambda *_: 8)
    if kind:
        assert fw.fn('ringnav_checkfw', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 8
    else:
        assert fw.fn('ringnav_reboot', C.c_int)() == 9
    assert len(fw.calls('save_memoryplay_info')) == saved_call
