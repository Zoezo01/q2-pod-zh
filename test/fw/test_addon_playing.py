"""patch/addon_playing.c: /tmp/q2-playing holds the card path of the song the player plays or pauses, for card apps
("playing" in /tmp/q2-addon.caps), and is gone otherwise."""
import ctypes as C

from conftest import Files
from test_onlinematch import Queue, records  # noqa: F401  the play queue and its songs

FILE = '/tmp/q2-playing'
STOPPED, PLAYING, PAUSED = 1, 2, 3  # mclGetPlayStatus


def test_the_song_playing_is_in_the_file_while_it_plays_or_pauses(fw, mcl, records):
    files, now = Files(fw), {'ms': 1000, 'status': STOPPED}
    fw.mock('time_now_ms', lambda *_: now['ms'])
    fw.mock('mclGetPlayStatus', lambda *_: now['status'])
    fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))
    poll = fw.fn('addon_playing_poll', None)
    Queue(fw, mcl, [records.song(b'/mnt/mmc/A/a.flac'), records.song(b'http://127.0.0.1:8096/s?token=t'),
                    records.song(b'/mnt/mmc/A/b.flac')])
    files.files[FILE] = b'/mnt/mmc/old.flac'  # a crashed run's

    def look(status=None, pos=None, ms=500):
        now['status'] = status or now['status']
        mcl.pos = mcl.pos if pos is None else pos
        now['ms'] += ms
        poll()
        return files.files.get(FILE)

    assert look() is None  # stopped: the last run's file goes at the first look
    assert look(PLAYING) == b'/mnt/mmc/A/a.flac'
    assert look(PAUSED) == b'/mnt/mmc/A/a.flac'
    assert look(pos=2, ms=100) == b'/mnt/mmc/A/a.flac'  # looked at every 500 ms
    assert look(ms=400) == b'/mnt/mmc/A/b.flac'
    files.files[FILE] = b'kept'
    assert look() == b'kept'  # written only on a change
    assert look(PLAYING, pos=1) is None  # a URL queue's song: never its address, which can carry a token
    assert look(pos=0) == b'/mnt/mmc/A/a.flac'
    assert look(pos=7) is None  # past the queue's end
    assert look(pos=0) == b'/mnt/mmc/A/a.flac'
    assert look(STOPPED) is None
