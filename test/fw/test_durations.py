"""Shared duration service: real C, fake card/TagLib/thread boundary, no stock ZIP needed."""
import ctypes as C
import random
import sqlite3
import struct

import pytest

from conftest import O, Files, claim
from test_nav_library_pages import Config, LENGTHS, Probe, lengths, path_key, song, st, scratch, ui  # noqa: F401
from test_playlist_art import real_sqlite

DUR_PAGE = 256


class Ds(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in ('blob', 'out', 'jobs', 'db')),
                *((k, C.c_uint) for k in ('n', 'out_n', 'total', 'cap', 'read', 'writable', 'pass_', 'whole')),
                ('last', C.c_int), ('thread', C.c_ulong),
                *((k, C.c_int) for k in ('running', 'done', 'cancel', 'exited', 'due'))]


def seconds(fw, record):
    return fw.fn('duration_seconds', C.c_int, C.c_void_p)(record)


def start(fw, ui, records):
    return fw.fn('durations_start', C.c_int, C.c_void_p)(ui.deques.new(records))


def busy(fw):
    return fw.fn('durations_busy', C.c_int, C.c_void_p, C.c_void_p)(None, None)


def finish(fw, probe):
    probe.run()
    assert busy(fw) == 0


def record(key, sec, stamp=(1000, 2000, 0)):
    return struct.pack('<Q3Ii', key, *stamp, sec)


def test_cache_is_looked_up_by_path_with_no_file_probes(fw, song):
    Files(fw, {LENGTHS: lengths((b'/m/a', 225), (b'/m/b', 3601))})
    assert [seconds(fw, song(p)) for p in (b'/m/a', b'/m/b', b'/m/c')] == [225, 3601, -1]
    assert not fw.calls('taglib_file_new') and not fw.calls('ringnav_statx')


@pytest.mark.parametrize('cue,end,want', [(0, 0, 1000), (30, 90, 60), (900, 0, 100), (1200, 0, 0), (-1, 0, 1000)])
def test_cue_duration_is_relative_to_the_track(fw, song, cue, end, want):
    Files(fw, {LENGTHS: lengths((b'/m/a', 1000))})
    assert seconds(fw, song(b'/m/a', cue=cue, end=end)) == want


@pytest.fixture
def stock_clock(fw):
    """toolsTimeItoa as docs/internals.md has stock's: %02d:%02d, and %02d:%02d:%02d from an hour."""
    def itoa(out, sec, *_):
        text = b'%02d:%02d:%02d' % (sec // 3600, sec // 60 % 60, sec % 60) if sec >= 3600 else b'%02d:%02d' % (sec // 60, sec % 60)
        C.memmove(out, text + b'\0', len(text) + 1)
    fw.mock('toolsTimeItoa', itoa)


@pytest.mark.parametrize('sec,want', [(0, b''), (225, b'03:45'), (3599, b'59:59'), (3600, b'01:00:00'), (36001, b'10:00:01')])
def test_duration_labels_are_stock_s(fw, song, stock_clock, sec, want):
    Files(fw, {LENGTHS: lengths((b'/m/a', sec))})
    out = C.create_string_buffer(b'junk', 24)
    fw.fn('duration_text', C.c_char_p, C.c_char_p, C.c_uint, C.c_void_p)(out, len(out), song(b'/m/a'))
    assert out.value == want and len(fw.calls('toolsTimeItoa')) == (sec > 0)


A = path_key(b'/m/a')


@pytest.mark.parametrize('data', [b'', b'old hash-only cache', b'Q2DUR1\0\0' + record(A, 1),  # #347's first format
                                  lengths((b'/m/a', 1))[:-1], b'Q2DUR2\0\0' + record(A, 1) + record(A, 2),
                                  b'Q2DUR2\0\0' + record(A + 1, 1) + record(A, 2), b'Q2DUR2\0\0' + record(A, -1)])
def test_bad_cache_is_rejected_whole(fw, song, data):
    Files(fw, {LENGTHS: data})
    assert seconds(fw, song(b'/m/a')) == -1
    assert fw.var('durations__ds', Ds).n == 0


def test_missing_cache_and_null_song(fw, song):
    Files(fw)
    assert seconds(fw, None) == -1 and seconds(fw, song(b'/m/a')) == -1
    assert fw.var('durations__ds', Ds).read == 1


def test_arbitrary_cache_bytes_are_safe(fw, song):
    files, rng = Files(fw), random.Random(0x1e4)
    state, a = fw.var('durations__ds', Ds), song(b'/m/a')
    for _ in range(800):
        data = rng.randbytes(rng.randrange(128))
        files.files[LENGTHS] = b'Q2DUR2\0\0' + data if rng.random() < 0.5 else data
        state.read, state.blob, state.n = 0, None, 0  # read again; the last load leaks, a test alone
        seconds(fw, a)


def test_long_songs_measures_with_the_switch_off_on_a_lowered_worker(fw, ui, song):
    Files(fw)
    Config(fw)
    probe = Probe(fw, {b'/m/a': 225})
    assert start(fw, ui, [song(b'/m/a')])
    probe.run()
    first = [name for name, _ in fw.calls() if name in ('cf_syscall', 'ringnav_statx')]
    assert first[:3] == ['cf_syscall'] * 3  # gettid, setpriority, ioprio_set: before the first stat
    assert busy(fw) == 0 and seconds(fw, song(b'/m/a')) == 225


def test_unchanged_file_is_statted_once_not_reparsed_or_rewritten(fw, ui, song):
    files = Files(fw, {LENGTHS: lengths((b'/m/a', 225))})
    probe = Probe(fw, {b'/m/a': 999})
    assert start(fw, ui, [song(b'/m/a')])
    finish(fw, probe)
    assert seconds(fw, song(b'/m/a')) == 225 and probe.opened == [] and len(fw.calls('ringnav_statx')) == 1
    assert not fw.calls('fw_fwrite') and files.files[LENGTHS] == lengths((b'/m/a', 225))


@pytest.mark.parametrize('stamp', [(1001, 2000, 0), (1000, 2001, 0), (1000, 2000, 1)])
def test_changed_file_replaces_old_duration(fw, ui, song, stamp):
    files = Files(fw, {LENGTHS: lengths((b'/m/a', 225))})
    probe = Probe(fw, {b'/m/a': 320})
    probe.stamps[b'/m/a'] = stamp
    assert start(fw, ui, [song(b'/m/a')])
    finish(fw, probe)
    assert probe.opened == [b'/m/a'] and seconds(fw, song(b'/m/a')) == 320
    assert files.files[LENGTHS] == lengths((b'/m/a', 320), stamp=stamp)


def test_a_file_that_changes_while_measured_keeps_its_old_length(fw, ui, song):
    Files(fw, {LENGTHS: lengths((b'/m/a', 225))})
    probe = Probe(fw, {b'/m/a': 320})
    probe.stamps[b'/m/a'] = (1001, 2000, 0)
    opened = probe.open
    fw.mock('taglib_file_new', lambda *a: probe.stamps.update({b'/m/a': (1002, 2000, 0)}) or opened(*a))
    assert start(fw, ui, [song(b'/m/a')])
    finish(fw, probe)
    assert seconds(fw, song(b'/m/a')) == 225  # measured again at the next pass


def test_duplicate_cue_paths_are_probed_once_and_jobs_own_paths(fw, ui, song):
    Files(fw)
    probe = Probe(fw, {b'/m/a': 900})
    a, b = song(b'/m/a', cue=0), song(b'/m/a', cue=500)
    records = ui.deques.new([a, b])
    assert fw.fn('durations_start', C.c_int, C.c_void_p)(records)
    del ui.deques.lists[records]  # caller's snapshot can go before the worker runs
    finish(fw, probe)
    assert probe.opened == [b'/m/a'] and [seconds(fw, r) for r in (a, b)] == [900, 400]


def test_one_shared_worker_and_results_published_only_after_join(fw, ui, song):
    Files(fw)
    probe = Probe(fw, {b'/m/a': 225})
    a = song(b'/m/a')
    assert start(fw, ui, [a]) and start(fw, ui, [a])
    assert len(probe.threads) == 1 and seconds(fw, a) == -1
    probe.run()
    assert seconds(fw, a) == -1
    busy(fw)
    assert seconds(fw, a) == 225 and len(fw.calls('fw_pthread_join')) == 1


def test_a_complete_pass_is_not_walked_again_until_the_library_changes(fw, ui, song):
    Files(fw)
    probe = Probe(fw, {b'/m/a': 225})
    assert start(fw, ui, [song(b'/m/a')])
    finish(fw, probe)
    assert not start(fw, ui, [song(b'/m/a')]) and len(probe.threads) == 1
    fw.var('library_gen', C.c_uint).value += 2  # an update rewrote it
    assert start(fw, ui, [song(b'/m/a')]) and len(probe.threads) == 2


def test_cancel_preserves_cached_records_and_does_not_join_a_running_worker(fw, ui, song):
    files = Files(fw, {LENGTHS: lengths((b'/m/a', 225))})
    probe = Probe(fw, {b'/m/b': 900})
    assert start(fw, ui, [song(b'/m/a'), song(b'/m/b')])
    fw.fn('durations_cancel', None)()
    assert busy(fw) == 1
    assert not fw.calls('fw_pthread_join')
    finish(fw, probe)
    assert probe.opened == [] and files.files[LENGTHS] == lengths((b'/m/a', 225))


def test_a_cancelled_pass_keeps_what_it_measured(fw, ui, song):
    files = Files(fw)
    probe = Probe(fw, {b'/m/a': 225, b'/m/b': 900})
    opened = probe.open
    fw.mock('taglib_file_new', lambda *a: fw.fn('durations_cancel', None)() or opened(*a))  # during the first file
    assert start(fw, ui, [song(b'/m/a'), song(b'/m/b')])
    finish(fw, probe)
    first, = probe.opened
    assert files.files[LENGTHS] == lengths((first, probe.seconds[first]))


def test_failed_stat_keeps_the_cached_duration(fw, ui, song):
    """A card gone under the worker (Standby, USB storage, pulled) fails every stat: the cache stays."""
    files = Files(fw, {LENGTHS: lengths((b'/m/a', 225))})
    probe = Probe(fw, {b'/m/a': 500})
    probe.stamps[b'/m/a'] = None
    a = song(b'/m/a')
    assert start(fw, ui, [a])
    finish(fw, probe)
    assert seconds(fw, a) == 225 and probe.opened == [] and not fw.calls('fw_fwrite')
    assert files.files[LENGTHS] == lengths((b'/m/a', 225))


def test_a_file_never_statted_rewrites_nothing(fw, ui, song):
    files = Files(fw, {LENGTHS: lengths((b'/m/a', 225))})
    probe = Probe(fw, {})
    probe.stamps[b'/m/gone'] = None  # deleted over USB while still in the library
    assert start(fw, ui, [song(b'/m/a'), song(b'/m/gone')])
    finish(fw, probe)
    assert not fw.calls('fw_fwrite') and files.files[LENGTHS] == lengths((b'/m/a', 225))


def test_a_file_over_2_gib_is_measured(fw, ui, song):
    Files(fw)
    probe = Probe(fw, {b'/m/dsd.dsf': 900})
    stat = probe.stat
    def big(dirfd, path, flags, mask, out, *_):
        stat(dirfd, path, flags, mask, out)
        (C.c_uint * 64).from_address(out)[11] = 1  # stx_size's high word: 4 GiB more
    fw.mock('ringnav_statx', big)
    assert start(fw, ui, [song(b'/m/dsd.dsf')])
    finish(fw, probe)
    assert seconds(fw, song(b'/m/dsd.dsf')) == 900


def test_removed_files_are_pruned_after_a_complete_scan(fw, ui, song):
    files = Files(fw, {LENGTHS: lengths((b'/m/a', 225), (b'/m/deleted', 900))})
    probe = Probe(fw, {b'/m/a': 225})
    assert start(fw, ui, [song(b'/m/a')])
    finish(fw, probe)
    assert seconds(fw, song(b'/m/deleted')) == -1 and files.files[LENGTHS] == lengths((b'/m/a', 225))


def test_a_failed_save_keeps_the_lengths_in_memory(fw, ui, song):
    files = Files(fw)
    fw.mock('fw_fwrite', lambda *_: 0)
    probe = Probe(fw, {b'/m/a': 700})
    assert start(fw, ui, [song(b'/m/a')])
    finish(fw, probe)
    assert seconds(fw, song(b'/m/a')) == 700 and LENGTHS not in files.files


def test_a_thread_that_cannot_start_releases_its_jobs(fw, ui, song):
    Files(fw)
    fw.mock('fw_pthread_create', lambda *_: 11)
    assert not start(fw, ui, [song(b'/m/a')])
    state = fw.var('durations__ds', Ds)
    assert not state.jobs and not state.running and not state.total


# Update Local Music's pass: Scan Song Durations on.
class Update:
    """A songtable of paths in a real SQLite file, the switch as given, a settable clock and USB page; scanned()
    is a library scan's end on its thread."""
    def __init__(self, fw, tmp_path, paths, switch=b'1'):
        self.fw, self.now, self.usb = fw, 5000, 0
        self.files = Files(fw)
        self.db = sqlite3.connect(tmp_path/'database.db')
        self.db.execute('create table songtable(id integer primary key, fileurl text)')
        self.db.executemany('insert into songtable(fileurl) values (?)', [(p.decode(),) for p in paths])
        self.db.commit()
        _, self.sqls = real_sqlite(fw, tmp_path/'database.db')
        self.probe = Probe(fw, dict.fromkeys(paths, 225))
        Config(fw, SCANDURATIONS=switch)
        fw.fn('pod_settings', None)()
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('navigator_window_is_exist', lambda name, *_: self.usb)
        self.state = fw.var('durations__ds', Ds)

    def scanned(self):
        self.fw.fn('durations_scanned', None)()

    def poll(self, n=1):
        for _ in range(n):
            self.now += 1000
            self.fw.fn('durations_poll', None)()

    def pages(self):
        return [s for s in self.sqls if s.startswith(b'select id,fileurl')]

    def bump(self):
        self.fw.var('library_gen', C.c_uint).value += 1


def test_neither_boot_nor_the_switch_alone_measures(fw, tmp_path, st):
    up = Update(fw, tmp_path, [b'/m/a'], switch=b'0')
    up.poll(3)
    st('scan_durations').value = 1  # turned on
    up.poll(3)
    assert not up.probe.threads and not up.sqls and not fw.calls('sqlite3_open')


def test_with_the_switch_off_an_update_measures_nothing(fw, tmp_path):
    up = Update(fw, tmp_path, [b'/m/a'], switch=b'0')
    up.scanned()
    up.poll(3)
    assert not up.probe.threads and not up.sqls


@pytest.mark.parametrize('stop', (0, 1, 2))  # Back; /mnt/data full
def test_update_local_music_and_a_folder_scan_ask_for_a_pass_unless_stopped(fw, stop):
    Config(fw, SCANDURATIONS=b'1')
    fw.fn('pod_settings', None)()
    fw.mock('toolsGetUserStop', lambda *_: stop)
    state = fw.var('durations__ds', Ds)
    for scan in ('coverflow_scan_all', 'coverflow_scan_folder'):
        state.due = 0
        fw.fn(scan, C.c_int, C.c_void_p, C.c_void_p)(None, None)
        assert state.due == (not stop), scan
    fw.fn('coverflow_delete_song', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert state.due == (not stop)  # a song deleted asks nothing


def test_an_update_reads_paths_alone_in_pages_then_measures_and_publishes(fw, tmp_path, song):
    paths = [b'/m/%04d.flac' % i for i in range(DUR_PAGE + 3)]
    up = Update(fw, tmp_path, paths)
    Files(fw, {LENGTHS: lengths((paths[0], 100), stamp=(1, 2, 3))})  # changed since
    up.scanned()
    up.poll()
    assert up.state.db and not up.probe.threads and not up.pages()  # opened; its first page next pass
    up.poll()
    assert len(up.pages()) == 1 and b'limit 256' in up.pages()[0] and b'audiobooks' in up.pages()[0]
    assert not up.probe.threads
    up.poll()
    assert len(up.pages()) == 2 and b'id>256 ' in up.pages()[1] and len(up.probe.threads) == 1
    assert not up.state.db and up.state.total == len(paths)
    assert not fw.calls('getAllMusic')  # no whole-library record copy
    up.probe.run()
    assert seconds(fw, song(paths[0])) == 100  # published at the join
    up.poll()
    assert seconds(fw, song(paths[0])) == 225 and seconds(fw, song(paths[-1])) == 225
    up.poll(3)
    assert len(up.probe.threads) == 1  # one pass an update


def test_pages_wait_for_input_quiet(fw, tmp_path, st):
    up = Update(fw, tmp_path, [b'/m/a'])
    up.scanned(), up.poll()
    st('last_input', C.c_uint).value = up.now + 500
    up.poll()
    assert not up.pages()
    up.poll()
    assert len(up.pages()) == 1


def test_a_running_pass_holds_standby_off(fw, tmp_path):
    up = Update(fw, tmp_path, [b'/m/a'])
    up.scanned(), up.poll(2)
    assert not fw.calls('reset_poweroptions_timer')
    up.poll()
    assert fw.calls('reset_poweroptions_timer')[-1][1][:3] == (1, 1, 0)
    up.probe.run()
    held = len(fw.calls('reset_poweroptions_timer'))
    up.poll(2)
    assert len(fw.calls('reset_poweroptions_timer')) == held


def test_the_switch_turned_off_cancels_a_pass_and_its_read(fw, tmp_path, ui, song):
    up = Update(fw, tmp_path, [b'/m/%04d.flac' % i for i in range(DUR_PAGE + 1)])
    up.scanned(), up.poll(2)
    assert up.state.db and up.state.total == DUR_PAGE
    fw.fn('durations_cancel', None)()  # pod_click's, on turning it off
    assert not up.state.db and not up.state.jobs and not up.state.total
    up.poll(3)
    assert not up.probe.threads and not up.state.db


def test_the_switch_off_leaves_long_songs_pass_running(fw, tmp_path, ui, song):
    up = Update(fw, tmp_path, [b'/m/a'], switch=b'0')
    assert start(fw, ui, [song(b'/m/a')])
    up.poll(2)
    assert up.state.running and not up.state.cancel and not up.pages()
    up.probe.run(), up.poll()
    assert seconds(fw, song(b'/m/a')) == 225


def test_long_songs_takes_over_an_update_s_read(fw, tmp_path, ui, song):
    up = Update(fw, tmp_path, [b'/m/%04d.flac' % i for i in range(DUR_PAGE + 1)])
    up.scanned(), up.poll(2)
    assert start(fw, ui, [song(b'/m/x')]) and up.state.total == 1 and not up.state.db


@pytest.fixture
def scanning():
    """Stock's library scan running, while the test says."""
    claim(O['SCAN_THREAD'])
    saved = C.string_at(O['SCAN_DONE'], 8)
    def set_(on):
        C.c_int.from_address(O['SCAN_THREAD']).value, C.c_int.from_address(O['SCAN_DONE']).value = on, 0
    yield set_
    C.memmove(O['SCAN_DONE'], saved, 8)


def test_an_update_s_read_waits_for_the_scan_s_end(fw, tmp_path, scanning):
    up = Update(fw, tmp_path, [b'/m/a'])
    scanning(1)
    up.scanned(), up.poll(2)
    assert not up.state.db
    scanning(0)
    up.poll(3)
    assert len(up.probe.threads) == 1


@pytest.mark.parametrize('away', ['usb', 'scan', 'change'])
def test_a_read_ends_for_usb_storage_a_scan_or_a_change(fw, tmp_path, scanning, away):
    up = Update(fw, tmp_path, [b'/m/%04d.flac' % i for i in range(DUR_PAGE + 1)])
    up.scanned(), up.poll(2)
    assert up.state.db
    if away == 'usb':
        up.usb = 1
        fw.fn('ringnav_usbmode', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)(None, None, None, None)
    elif away == 'scan':
        scanning(1)
    else:
        up.bump()  # a song deleted meanwhile
    up.poll()
    assert not up.state.db and not up.state.total
    up.usb = 0
    scanning(0)
    up.poll(3)
    assert len(up.probe.threads) == (away == 'change')  # a scan's end asks again itself


@pytest.mark.parametrize('away', ['usb', 'scan'])
def test_usb_storage_or_a_scan_cancels_a_running_pass(fw, tmp_path, scanning, away):
    up = Update(fw, tmp_path, [b'/m/a'])
    up.scanned(), up.poll(3)
    assert up.state.running
    if away == 'usb':
        up.usb = 1
    else:
        scanning(1)
    up.poll()
    assert up.state.running and up.state.cancel
    up.probe.run()
    up.poll()
    assert not up.state.running


def test_usb_storage_stops_the_duration_worker_before_stock_hands_over_the_card(fw):
    """A pass may hold a card file open in TagLib: USB Storage joins the worker before stock's page."""
    state = fw.var('durations__ds', Ds)
    state.running, state.thread = 1, 0x78
    order: list = []
    fw.mock('fw_usleep', lambda *_: setattr(state, 'exited', 1))  # the current file returns
    fw.mock('fw_pthread_join', lambda t, *_: order.append(('join', t, state.cancel)) and 0)
    fw.mock('stock_usbmode_trampoline', lambda *_: order.append(('stock', state.running)) or 3)
    assert fw.fn('ringnav_usbmode', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)(None, None, None, None) == 3
    assert ('join', 0x78, 1) in order and order[-1] == ('stock', 0)


def test_a_clean_end_waits_about_five_seconds_for_a_wedged_file(fw):
    state = fw.var('durations__ds', Ds)
    state.running, state.thread = 1, 0x78
    fw.fn('durations_shutdown', None)()
    assert len(fw.calls('fw_usleep')) == 500 and not fw.calls('fw_pthread_join') and state.running


# Minimal's and Modern's text track list: one row height a list, as stock's layouter forces item_height on each row.
@pytest.mark.parametrize('end,want', [(200, [('One', '03:20'), ('Two', '')]), (0, [('One', None), ('Two', None)])])
def test_a_track_list_with_any_length_gives_every_row_the_detail_height(fw, ui, song, stock_clock, end, want):
    from test_coverflow import Cf
    Files(fw)
    tracks = [song(b'/m/a.flac', b'One', track=1, end=end), song(b'/m/a.flac', b'Two', track=2, cue=end)]
    directory = fw.var('fw_ctx_tools_pdeq_directory', C.c_uint)
    fw.mock('getMusicByAlbum', lambda *_: ui.deques[directory.value].extend(tracks) or 2)
    page = ui.new('window_create', 0, 0, 0, 0, 0)
    c = fw.var('cf', Cf)
    c.page, c.body, c.scoped, c.album = page, page, 2, 0  # Rated albums: no last-album note to write
    c.albums = ui.deques.new([song(id_=-1)])  # Unknown Album: every track getMusicByAlbum(NULL) has
    fw.fn('coverflow__to_tracks', C.c_int, C.c_void_p)(None)
    assert ui.rows(page) == want  # the last CUE track's file never measured: an empty detail
    items = ui.kids[ui.view(page)]
    boxes = [struct.unpack_from('<4i', ui.blocks[w]) for w in items]
    h = boxes[0][3]
    assert h == (fw.fn('page_h', C.c_int, C.c_int)(64 if end else 48)) and [b[1] for b in boxes] == [0, h]
    assert all(b[3] == h for b in boxes)
