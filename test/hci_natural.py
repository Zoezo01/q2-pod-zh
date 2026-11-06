#!/usr/bin/env python3
"""Natural track changes in the real hciplayer (hci_emu.py runs it, its threads, demuxers and decoders on Unicorn with
ALSA modelled and a virtual clock), driven over its socket as demo drives it.

A preloaded change is bit-exact. A next file no demuxer knows leaves the song playing to its last frame, then reports;
a real stop landing in that failed open still stops. A header that understates the length cuts nothing. A preload the
song outran or the queue closed is dropped: the next song is the right one. A header's frame count ends an MP3 followed
by junk. A seek near a file's end keeps that end, on every format. Under Crossfade a late next takes the held tail up to
30 s on, and the fade flag moved mid-track moves the mix as test/xfade_player.py's filter says.
Usage: test/hci_natural.py BUILD_DIR [PLAYER]  (PLAYER: another hciplayer, to see old code fail)"""
import array, math, pathlib, random, shutil, struct, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_RA, UC_MIPS_REG_S2, UC_MIPS_REG_V1
from hci_emu import Emu

build = pathlib.Path(sys.argv[1]).resolve()
player = pathlib.Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else build/'peq'/'hciplayer'
work, root, aud = build/'hci_natural', build/'hci_natural'/'root', build/'hci_natural'/'aud'
AUTH, STOP, NEXT = 0x43084c, 0xb10e70, 0xb10de8  # the licence check; the stop flag; the preload's state
TOUCH_NOTSUPPORT, COLD_PATH = 0x4234e8, 0x421a3c
LAVF_CHECK, LAVF_PREFERRED, FREE_PRELOAD = 0x4ae664, 0x4af03c, 0x42a9ac  # lavf's check; from the preferred one, stock's first try; the thread's free of its context
SECONDS, RATE = 5, 44100
N = SECONDS * RATE
LONG_SECONDS = 10
LONG_N = LONG_SECONDS * RATE
SEEKS = (('s44', 44100, 215 * 4096 + 3000, 4096), ('s1152', 44100, 765 * 1152 + 700, 1152), ('s96', 96000, 8 * 96000 + 2000, 4096),
         ('s192', 192000, 5 * 192000 + 3000, 8192))
OTHERS = {'x.mp3': ['-c:a', 'libmp3lame', '-q:a', '2'], 'h.mp3': ['-c:a', 'libmp3lame', '-q:a', '2', '-write_xing', '0'],
          'x.m4a': ['-c:a', 'aac'], 'x.ogg': ['-c:a', 'libvorbis'], 'x.wv': ['-c:a', 'wavpack'], 'x.flac': ['-c:a', 'flac'],
          'x.wav': ['-c:a', 'pcm_s16le'], 'x.wma': ['-c:a', 'wmav2'], 'l.mp3': ['-c:a', 'libmp3lame', '-b:a', '320k'],
          'l.m4a': ['-c:a', 'aac'], 'la.m4a': ['-c:a', 'alac'], 'l.ogg': ['-c:a', 'libvorbis'], 'l.wv': ['-c:a', 'wavpack'], 'l.wav': ['-c:a', 'pcm_s16le']}
ELF = build/'peq'/'peq.elf'


def setup():
    shutil.rmtree(work, ignore_errors=True)
    aud.mkdir(parents=True)
    subprocess.run(['unsquashfs', '-no-progress', '-d', root, '-f', build/'stock.squashfs', 'lib', 'usr/lib'], check=True, stdout=subprocess.DEVNULL)
    for seed, name in enumerate('abc', 1):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'anoisesrc=d={SECONDS}:c=pink:r={RATE}:seed={seed}:a=0.4',
                        '-ac', '2', '-c:a', 'pcm_s16le', aud/f'{name}.wav'], check=True)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', aud/f'{name}.wav', '-c:a', 'flac', aud/f'{name}.flac'], check=True)
    for name in 'junk.flac', 'junk.dff', 'junk.iso':
        (aud/name).write_bytes(random.Random(1).randbytes(200000))
    (aud/'empty.flac').write_bytes(b'')
    h = bytearray((aud/'a.flac').read_bytes()[:42])  # STREAMINFO, the last metadata block, no rate or length, no frames
    h[4] |= 0x80
    h[18:26] = bytes(8)
    (aud/'headers.flac').write_bytes(h)
    g = bytes((aud/'a.flac').read_bytes()[:42])          # the same with a good rate and length, and the last-block bit
    (aud/'headers2.flac').write_bytes(g[:4] + bytes([g[4] | 0x80]) + g[5:])
    # a.flac with STREAMINFO's total samples halved: a header that understates the length
    b = bytearray((aud/'a.flac').read_bytes())
    value = int.from_bytes(b[18:26], 'big')
    b[18:26] = ((value & ~((1 << 36) - 1)) | N // 2).to_bytes(8, 'big')
    (aud/'under.flac').write_bytes(b)
    junk = random.Random(2).randbytes(3 << 20)
    (aud/'big.flac').write_bytes(junk)
    (aud/'big.aac').write_bytes(junk)
    # an ASF header object with no streams in it, then its data object
    guid = bytes.fromhex('3026b2758e66cf11a6d900aa0062ce6c')
    (aud/'nostreams.wma').write_bytes(guid + struct.pack('<QIH', 30, 0, 0x0201) + bytes.fromhex('3626b2758e66cf11a6d900aa0062ce6c') +
                                      struct.pack('<Q', 50) + bytes(34) + junk[:60000])
    # low level, so 64 KB, the FLAC seek's step, is about 2 s; the last frame short, so its time by its own size is far early
    def wav(name, rate, frames, seed=1):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'anoisesrc=r={rate}:c=pink:a=0.0005:seed={seed}', '-af',
                        f'atrim=end_sample={frames}', '-ac', '2', '-c:a', 'pcm_s16le', aud/f'{name}.wav'], check=True)
    for name, rate, frames, block in SEEKS:
        wav(name, rate, frames, 3)
        subprocess.run(['flac', '-s', '-f', '--no-seektable', f'--blocksize={block}', '-o', aud/f'{name}.flac', aud/f'{name}.wav'], check=True)
    s = bytearray((aud/'s44.flac').read_bytes())   # STREAMINFO's maximum above the stream's fixed block
    s[10:12] = (8192).to_bytes(2, 'big')
    (aud/'s44m.flac').write_bytes(s)
    (aud/'s44m.wav').write_bytes((aud/'s44.wav').read_bytes())
    for name, frames in (('t20', RATE // 50), ('t100', RATE // 10)):  # a whole track's worth of very short ones
        wav(name, RATE, frames, 6)
        subprocess.run(['flac', '-s', '-f', '-o', aud/f'{name}.flac', aud/f'{name}.wav'], check=True)
    a = (aud/'b.flac').read_bytes()                  # a next FLAC with big metadata: 1 MB of padding before its frames
    (aud/'bigmeta.flac').write_bytes(a[:4] + bytes([a[4] & 0x7f]) + a[5:42] + bytes([0x81]) + (1 << 20).to_bytes(3, 'big') + bytes(1 << 20) + a[42:])
    (aud/'cutinfo.flac').write_bytes((aud/'a.flac').read_bytes()[:30])    # cut inside STREAMINFO
    c = (aud/'a.flac').read_bytes()
    (aud/'cutblock.flac').write_bytes(c[:4] + bytes([c[4] & 0x7f]) + c[5:42])  # cut where the next metadata block's header should be
    wav('nextq', RATE, 8 * RATE, 4)
    subprocess.run(['flac', '-s', '-f', '-o', aud/'nextq.flac', aud/'nextq.wav'], check=True)
    # the other formats, 8 s, and the MP3s' shapes
    wav8 = aud/'loud.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'anoisesrc=d=8:c=pink:r={RATE}:seed=5:a=0.4', '-ac', '2',
                    '-c:a', 'pcm_s16le', wav8], check=True)
    for name, args in OTHERS.items():
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', aud/'a.wav' if name.startswith(('x', 'h')) else wav8, *args, aud/name], check=True)
    for name in 'x.mp3', 'h.mp3', 'x.m4a', 'x.ogg', 'x.wv', 'x.flac', 'x.wav', 'x.wma':
        (aud/f'junk{name}').write_bytes((aud/name).read_bytes() + junk)
    def frames_field(data, change):
        at = data.find(b'Xing', 0, 300)
        at = at if at >= 0 else data.find(b'Info', 0, 300)
        count = int.from_bytes(data[at + 8:at + 12], 'big')
        return data[:at + 8] + (count + change).to_bytes(4, 'big') + data[at + 12:]
    x = (aud/'x.mp3').read_bytes()
    (aud/'off.mp3').write_bytes(frames_field(x, -19))    # 0.5 s short: the audio still plays out
    (aud/'cut.mp3').write_bytes(frames_field(x, -115))   # 3 s short: still plays out, frames chain past the count
    id3, id1 = b'ID3\x04\0\0\0\0\x10\0' + bytes(2048), b'TAG' + bytes(125)
    (aud/'joined.mp3').write_bytes(id3 + x + id1 + id3 + x + id1)   # two tagged MP3s, joined with cat
    (aud/'junkjoined.mp3').write_bytes(x + x + junk)
    def ape(n):                                                     # an APEv2 tag holding a cover
        item = struct.pack('<II', n, 2) + b'Cover Art (Front)\0cover.jpg\0' + random.Random(2).randbytes(n - 10)
        foot = lambda flags: b'APETAGEX' + struct.pack('<IIII', 2000, len(item) + 32, 1, flags) + bytes(8)
        return foot(0xa0000000) + item + foot(0x80000000)
    (aud/'apex.mp3').write_bytes(x + ape(3 << 20) + id1)
    for seed, name in enumerate(('long_a', 'long_b', 'long_c'), 7):  # long enough that a fade's window opens after the first packet's position
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'anoisesrc=d={LONG_SECONDS}:c=pink:r={RATE}:seed={seed}:a=0.4',
                        '-ac', '2', '-c:a', 'pcm_s16le', aud/f'{name}.wav'], check=True)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', aud/f'{name}.wav', '-c:a', 'flac', aud/f'{name}.flac'], check=True)
    # DSD: a DSF whose header states half its samples
    def dsf(name, samples):
        body = random.Random(3).randbytes(4096 * 2 * 690)  # 5 s
        (aud/name).write_bytes(b'DSD ' + struct.pack('<QQQ', 28, 92 + len(body), 0) + b'fmt ' + struct.pack('<QIIIIIIQI', 52, 1, 0, 2, 2, 2822400, 1, samples, 4096) +
                               bytes(4) + b'data' + struct.pack('<Q', 12 + len(body)) + body)
    dsf('whole.dsf', 690 * 4096 * 8)
    dsf('under.dsf', 345 * 4096 * 8)


class Player:
    def __init__(self):
        self.e = e = Emu(player, root, work, log=work/'player.log', fsmap={'/mnt/mmc': str(aud)})
        e.hook_fn(AUTH, lambda e: 0)
        for name in 'mnt_data_peq-active', 'tmp_q2xfade':
            (e.tmp/name).unlink(missing_ok=True)
        self.pcm = bytearray()
        e.on_write = lambda e, data, n, p: self.pcm.extend(data)
        e.alsa_active = True
        self.run(0.3)
    def run(self, s):
        self.e.run(until=self.now() + s)
    def now(self):
        return (self.e.clock - 10**12) / 1e9
    def frames(self):
        return len(self.pcm) // 4
    def song(self, name):
        self.e.send_cmd('{set-songurl\\/mnt/mmc/%s|ss=0|0|0|1|0|90}' % name)
    def gapless(self, name):
        self.e.send_cmd('{set-gapless\\/mnt/mmc/%s|ss=0|0|0|1|90}' % name)
    def until(self, done, limit, step=0.02):
        """Run in small steps until done() or limit virtual seconds on; False if it never came."""
        end = self.now() + limit
        while self.now() < end:
            if done(): return True
            self.run(step)
        return done()
    def events(self, *names):
        return [(n, k) for _, n, k in self.e.events if n in names]
    def opened(self, name):
        return sum(1 for _, n, k in self.e.events if n == 'open' and k['path'].endswith(name))
    def fds(self, name):
        return sum(1 for f in self.e.fds.values() if f.get('path', b'').endswith(name.encode()))
    def seek(self, seconds):
        self.e.send_cmd('{mcl-seek\\%d}' % seconds)
    def seek_drop(self, seconds):
        """Seek, and return the frames out when it dropped the PCM: what's after that is what the seek played."""
        drops = len(self.events('pcm_drop'))
        self.seek(seconds)
        self.until(lambda: len(self.events('pcm_drop')) > drops, 1)
        return self.events('pcm_drop')[drops][1]['total'] if len(self.events('pcm_drop')) > drops else self.frames()
    def settled(self, limit):
        """Run until the output hasn't grown for 1 s."""
        end, last, since = self.now() + limit, -1, self.now()
        while self.now() < end and self.now() - since < 1:
            if self.frames() != last: last, since = self.frames(), self.now()
            self.run(0.1)
    def eq(self):
        """A live filter, flat but for a band at 0 dB: the path a filter sits on, without changing the music."""
        preset = struct.pack('<iid', 1, 0, 0.0) + struct.pack('<iiddd', 1, 0, 1000.0, 0.0, 1.0) + struct.pack('<iiddd', 0, 0, 1000.0, 0.0, 1.41) * 29 + struct.pack('<dii', 0.0, 1, 0)
        (self.e.tmp/'mnt_data_peq-active').write_bytes(b'Q2PEQ02\0' + preset)
        self.e.send_cmd('{mcl-seteq\\0|0|0|0|0|0|0|0|0|0|1}')


def source(name):
    return (aud/f'{name}.wav').read_bytes()[-N * 4:]


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what, flush=True)
    failures.extend([] if ok else [what])


def seam():
    """A preloaded change at the same format: every frame of both, in order, nothing between."""
    p = Player()
    p.song('a.flac')
    p.run(1)
    p.gapless('b.flac')
    p.until(lambda: p.frames() >= 2 * N, 2 * SECONDS + 2)
    check(bytes(p.pcm[:2 * N * 4]) == source('a') + source('b'), f'seam: {p.frames()} frames, a then b bit-exact')
    check(p.opened('b.flac') == 1, 'seam: b opened once, by the preload')


def bad_next(name, site=None):
    """The next file opens and fails its probe: a plays to its last frame, then the player reports."""
    p = Player()
    tids, lavf, hit, forced = set(), [], [], []
    p.e.watch_fn(0x4223c8, lambda e: tids.add(e.cur.tid))
    p.e.watch_fn(LAVF_CHECK, lambda e: lavf.append(e.cur.tid) if e.u.reg_read(UC_MIPS_REG_RA) != LAVF_PREFERRED else None)
    # either side of the thread's forced checks
    p.e.watch_fn(0x485aec, lambda e: forced.append(e.u.reg_read(UC_MIPS_REG_S2)) if e.cur.tid != 1 else None)
    p.e.watch_fn(0x485af4, lambda e: forced.append(e.u.reg_read(UC_MIPS_REG_S2)) if e.cur.tid != 1 else None)
    if site: p.e.watch_fn(site, lambda e: hit.append(e.cur.tid))
    p.song('a.flac')
    p.run(1)
    p.gapless(name)
    stopped, said = [0], bytearray()
    def reported():
        stopped[0] |= p.frames() < N and p.e.r32(STOP)
        said.extend(b''.join(p.e.replies))
        p.e.replies.clear()
        p.e.send_cmd('{player-status\\request}')
        return b'error' in said
    check(p.until(reported, SECONDS + 1), f'{name}: reported: {said[-60:]}')
    check(not [t for t in lavf if t != 1], f'{name}: the thread gave up before the list reached lavf ({lavf})')
    check(forced[::2] == forced[1::2], f"{name}: a forced check leaves its caller's $18 ({forced})")
    if site: check([t for t in hit if t != 1], f'{name}: the thread reached {site:#x} ({hit})')
    check(not stopped[0], f'{name}: the stop flag stays clear while a plays')
    check(p.frames() == N and bytes(p.pcm) == source('a'), f'{name}: a whole, {p.frames()} of {N} frames')
    check(said.index(b'exit') < said.index(b'error') if b'exit' in said else False, f'{name}: its end, then the error')
    p.run(0.1)
    check(p.e.r32(NEXT) == 0 and tids == {1}, f'{name}: the main thread opened it (state {p.e.r32(NEXT):#x}, tids {tids})')
    p.song('b.flac')  # demo's Next after the prompt
    base = p.frames()
    p.until(lambda: p.frames() >= base + RATE, 3)
    check(bytes(p.pcm[base * 4:(base + RATE) * 4]) == source('b')[:RATE * 4], f'{name}: the next song then plays')


def exits(p, seconds):
    """Virtual times of the exits hciplayer sends in the next `seconds`, asking as demo does."""
    at = []
    def tick():
        at.extend(p.now() for r in p.e.replies if b'exit' in r for _ in range(r.count(b'exit')))
        p.e.replies.clear()
        p.e.send_cmd('{player-status\\request}')
    end = p.now() + seconds
    while p.now() < end:
        p.run(0.01)
        tick()
    return at


def next_ends_at_once(name, gap):
    """A next file that ends as it starts: its exit comes no sooner than demo can read it after the song's. A real short
    track's audio is all there; ordinary tracks' exits aren't touched."""
    p = Player()
    p.song('a.flac')
    p.run(1)
    p.gapless(name)
    at = exits(p, SECONDS + 1)
    check(len(at) >= 2 and at[1] - at[0] >= gap - 0.011, f'{name}: exits {[round(t, 3) for t in at]}, wanted {gap} s apart')
    check(bytes(p.pcm[:N * 4]) == source('a'), f'{name}: a whole')
    return p


def exit_apart():
    next_ends_at_once('headers2.flac', 0.3)
    for name in 't20', 't100':
        p = next_ends_at_once(name + '.flac', 0.3)
        want = (aud/f'{name}.wav').read_bytes()[-(len(p.pcm) - N * 4):] if len(p.pcm) > N * 4 else b''
        check(len(want) and bytes(p.pcm[N * 4:N * 4 + len(want)]) == want, f'{name}: its audio follows a, {len(p.pcm) // 4 - N} frames')
    p = Player()
    p.song('a.flac')
    p.run(1)
    p.gapless('b.flac')
    p.run(SECONDS - 2)
    p.gapless('c.flac')
    at = exits(p, SECONDS + 1)
    check(len(at) >= 1 and bytes(p.pcm[:N * 4]) == source('a') and bytes(p.pcm[N * 4:2 * N * 4]) == source('b'), 'ordinary tracks: bit-exact')


def metadata_cut():
    """A FLAC that ends where a metadata header should be: the walk ends, as next and picked directly, not the same blocks
    forever. On stock this test doesn't return."""
    for name in 'cutinfo.flac', 'cutblock.flac':
        next_ends_at_once(name, 0.3)
        p = Player()
        p.song(name)
        at = exits(p, 2)
        check(len(at) >= 1 and p.frames() == 0, f'{name} directly: exits {at}, {p.frames()} frames')


def music_info_no_stream():
    """get-music-info, on the command thread, while the main thread has no sh_audio: the no-stream reply, not a fault. The loads
    are forced to NULL on that thread alone, since the race itself can't be timed here. Stock faults at 0x42c094, then 0x42c064."""
    for name, load, length in ('sh_audio', 0x42c068, 1), ('mpctx after the length call', 0x42c588, 0):
        p = Player()
        p.song('a.flac')
        p.run(1)
        if not length: p.e.w32(0xb10e84, 0)
        p.e.watch_fn(load, lambda e: e.u.reg_write(UC_MIPS_REG_V1, 0) if e.cur.tid != 1 else None)
        p.e.replies.clear()
        p.e.send_cmd('{media-info\\get-music-info}')
        before = p.frames()
        p.run(0.5)
        said = b''.join(p.e.replies)
        check(b'no-playing-status' in said, f'music info, no {name}: {said[-50:]}')
        check(p.frames() > before, f'music info, no {name}: playback carries on')


def eq_enable_no_stream():
    """mcl-seteq's enable with nothing playing: no sh_audio to read. Stock faults at 0x42cec4 or 0x42cec8. A song then plays."""
    p = Player()
    p.eq()
    p.run(0.2)
    check(p.e.r32(0xb10e08) == 2, f'EQ enable, no stream: state {p.e.r32(0xb10e08)}')
    p.song('a.flac')
    p.run(1)
    check(p.frames() > 0, f'EQ enable, no stream: a song then plays, {p.frames()} frames')


def next_whole(name):
    """A next file's first samples aren't lost to the preload: the same as it opened directly."""
    p = Player()
    p.song(name)
    p.settled(SECONDS + 12)
    cold = bytes(p.pcm)
    p = Player()
    p.song('a.flac')
    p.run(1)
    p.gapless(name)
    p.until(lambda: p.frames() > N + RATE, SECONDS + 2)
    p.settled(SECONDS + 14)
    got = bytes(p.pcm[N * 4:])
    check(len(got) == len(cold) and got == cold, f'{name}: next {len(got)} bytes, directly {len(cold)}')


def stop_in_failed_open():
    """demo's stop while the thread's open of a bad file is under way isn't lost."""
    p = Player()
    p.e.hooks['delay'] = lambda raw: 0.4 if raw.endswith(b'junk.flac') else 0
    p.song('a.flac')
    p.run(1)
    p.gapless('junk.flac')
    p.run(0.1)
    p.e.send_cmd('{mcl-stop\\null}')
    p.until(lambda: p.events('pcm_close', 'pcm_drop'), 1)
    before = p.frames()
    p.run(1)
    check(p.frames() == before and before < N, f'stop in a failed open: stopped at {before} frames and stayed stopped')


def understated():
    """The header says half the audio: all of it plays, and the next change is still exact."""
    p = Player()
    p.song('under.flac')
    p.run(1)
    p.gapless('b.flac')
    p.until(lambda: p.frames() >= 2 * N, 2 * SECONDS + 2)
    check(bytes(p.pcm[:N * 4]) == source('a'), f'understated: all {N} frames of the file played (forced EOF would stop at about {N // 2 + 2 * RATE})')
    check(bytes(p.pcm[N * 4:2 * N * 4]) == source('b'), 'understated: the next song follows whole')


def late(wait):
    """set-gapless less than an open before demux EOF: b cold-starts, the stale thread frees its context, and c,
    sent wait seconds on, once that thread is done or (0) while it still runs, plays: it's not b again."""
    p = Player()
    p.e.hooks['delay'] = lambda raw: 1.0 if raw.endswith(b'b.flac') and p.opened('b.flac') == 1 else 0  # the thread's
    freeing = []
    p.e.watch_fn(FREE_PRELOAD, lambda e: freeing.append(e.r32(NEXT)) if e.cur.tid != 1 else None)
    p.song('a.flac')
    p.until(lambda: p.frames() > N - RATE // 5, SECONDS + 1, 0.002)
    p.gapless('b.flac')
    check(p.until(lambda: p.frames() > N + 2000, 2), f'late: b cold-started at frame {p.frames()}')
    p.run(wait)
    if wait: check(p.fds('b.flac') == 1, f"late, c {wait} s on: the stale thread closed its file ({p.fds('b.flac')} open)")
    p.gapless('c.flac')
    p.run(1.3)
    check(freeing and set(freeing) == {3}, f'late, c {wait} s on: the word stays 3 while the thread frees ({freeing})')
    check(p.e.r32(NEXT) == 2, f'late, c {wait} s on: preloaded (state {p.e.r32(NEXT):#x})')
    p.until(lambda: p.frames() >= 3 * N, 2 * SECONDS + 3)
    b, c = bytes(p.pcm[N * 4:2 * N * 4]), bytes(p.pcm[2 * N * 4:3 * N * 4])
    check(b == source('b') and c == source('c'), f'late, c {wait} s on: third song is c, not b again ({p.frames()} frames; b exact {b == source("b")}, third==c {c == source("c")}, third==b {c == source("b")})')
    check(p.opened('b.flac') == 2 and p.opened('c.flac') == 1, f"late, c {wait} s on: opens b {p.opened('b.flac')}, c {p.opened('c.flac')}")


def late_failed():
    """The same race on a bad file: the failed thread's store doesn't block the next preload."""
    p = Player()
    p.e.hooks['delay'] = lambda raw: 0.7 if raw.endswith(b'junk.flac') and p.opened('junk.flac') == 1 else 0
    p.song('a.flac')
    p.until(lambda: p.frames() > N - RATE // 5, SECONDS + 1, 0.002)
    p.gapless('junk.flac')
    p.until(lambda: p.frames() == N, 1)
    p.run(0.3)
    p.song('b.flac')
    p.until(lambda: p.frames() > N + 2000, 2)
    p.run(1.2)
    check(p.e.r32(NEXT) == 0, f'late failure: state {p.e.r32(NEXT):#x} once its thread is done, so the next preload can start')
    p.gapless('c.flac')
    p.run(0.3)
    check(p.opened('c.flac') == 1, 'late failure: the next preload starts')


def second_cold():
    """A second cold start while a stale thread still runs: still 3, so nothing starts on its context."""
    p = Player()
    p.e.hooks['delay'] = lambda raw: 7.0 if raw.endswith(b'b.flac') and p.opened('b.flac') == 1 else 0
    clears = []
    p.e.watch_fn(COLD_PATH, lambda e: clears.append(e.r32(NEXT)))
    p.song('a.flac')
    p.until(lambda: p.frames() > N - RATE // 5, SECONDS + 1, 0.002)
    p.gapless('b.flac')
    p.until(lambda: p.frames() > N + 2000, 2)
    check(p.e.r32(NEXT) == 3, f'second cold: 3 after the first ({p.e.r32(NEXT):#x})')
    p.gapless('c.flac')  # waits for the word; b's end comes first and starts c cold
    p.until(lambda: p.frames() > 2 * N + 2000, SECONDS + 1)
    check(clears[-1] == 3 and p.e.r32(NEXT) == 3, f'second cold: 3 after the second ({clears}, {p.e.r32(NEXT):#x})')
    check(p.fds('c.flac') == 1 and bytes(p.pcm[2 * N * 4:(2 * N + 2000) * 4]) == source('c')[:8000], 'second cold: c plays')
    p.until(lambda: p.e.r32(NEXT) == 0, 4)
    check(p.e.r32(NEXT) == 0, f'second cold: 0 once the thread is done ({p.e.r32(NEXT):#x})')


def symbol(name):
    for line in subprocess.run(['llvm-nm', ELF], capture_output=True, text=True).stdout.splitlines():
        if line.endswith(' ' + name): return int(line.split()[0], 16)
    raise SystemExit(f'no {name} in {ELF}')


def inject(p, name, frm, to):
    """Just before name's first sc, the word moves from frm to to, as another thread's store would. Counts on Unicorn's sc
    failing when the word isn't what ll read, as the hardware's does when it's written."""
    addr = symbol(name)
    while struct.unpack('<I', p.e.u.mem_read(addr, 4))[0] >> 26 != 0x38: addr += 4
    done = []
    def go(e):
        if not done and e.r32(NEXT) == frm:
            e.w32(NEXT, to)
            done.append(1)
    p.e.watch_fn(addr, go)
    return done


def race_publish():
    """The cold path's store lands between the thread's ll and sc: the thread goes stale, it doesn't take it as published."""
    p = Player()
    done = inject(p, 'preload_publish', 1, 3)
    p.song('a.flac')
    p.run(1)
    p.gapless('b.flac')
    p.run(1)
    # b's URL is still set, so the main loop starts it again once the word is 0
    check(done and p.e.r32(NEXT) == 2 and p.opened('b.flac') == 2 and p.fds('b.flac') == 1, f"race at publish: retried, freed, started again ({p.e.r32(NEXT):#x}, opens {p.opened('b.flac')}, open {p.fds('b.flac')}, injected {done})")


def race_cold():
    """The thread's publish lands between the cold path's ll and sc: the retry sees 2 and clears it."""
    p = Player()
    p.e.hooks['delay'] = lambda raw: 0.5 if raw.endswith(b'b.flac') and p.opened('b.flac') == 1 else 0
    done = inject(p, 'cold_clear', 1, 2)
    p.song('a.flac')
    p.until(lambda: p.frames() > N - RATE // 5, SECONDS + 1, 0.002)
    p.gapless('b.flac')
    p.until(lambda: p.frames() > N + 2000, 2)
    p.run(1)
    check(done and p.e.r32(NEXT) == 0, f'race at the cold path: retried to 0 ({p.e.r32(NEXT):#x}, injected {done})')


def close_while_open(again):
    """mcl-closegapless while the thread opens b, as a queue edit does: b isn't played; c, if sent, is."""
    p = Player()
    p.e.hooks['delay'] = lambda raw: 1.0 if raw.endswith(b'b.flac') else 0
    done = inject(p, 'preload_close', 1, 2) if again == 'race' else []
    p.song('a.flac')
    p.run(0.5)
    p.gapless('b.flac')
    p.run(0.2)
    p.e.send_cmd('{mcl-closegapless\\null}')
    p.run(0.1)
    if again == 'race':
        check(done and p.e.r32(NEXT) == 0, f'close, raced: retried and cleared ({p.e.r32(NEXT):#x}, injected {done})')
        return
    check(p.e.r32(NEXT) == 3, f'close: the thread is stale ({p.e.r32(NEXT):#x})')
    if again: p.gapless('c.flac')
    p.run(1.5)
    check(p.fds('b.flac') == 0, f"close: its file is closed ({p.fds('b.flac')} open)")
    if not again:
        check(p.e.r32(NEXT) == 0, f'close, nothing next: state clear ({p.e.r32(NEXT):#x})')
        return
    p.until(lambda: p.frames() >= 2 * N, 2 * SECONDS + 3)
    second = bytes(p.pcm[N * 4:2 * N * 4])
    check(bytes(p.pcm[:N * 4]) == source('a') and second == source('c'), f"close, then c: c follows a, not b (c {second == source('c')}, b {second == source('b')})")
    check(p.opened('c.flac') == 1, f"close, then c: c opened {p.opened('c.flac')}")


def iso_no_tracks():
    """A SACD ISO with a good TOC and no tracks (0x478b40), next: the check's hooks return a TOC of our own, as making a real
    one for this one branch isn't worth it. The stop stays clear, and it's reported at a's end."""
    p = Player()
    toc, area = 0x6fe00000, 0x6fe00800
    p.e.ensure(toc, 0x1000)
    p.e.w32(toc + 0x68, area)
    hit = []
    p.e.watch_fn(0x478b40, lambda e: hit.append(e.cur.tid))
    p.e.hook_fn(0x46a010, lambda e: toc)
    p.e.hook_fn(0x46bd18, lambda e: 0)
    p.e.hook_fn(0x469e90, lambda e: 0)
    p.song('a.flac')
    p.run(1)
    p.gapless('junk.iso')
    stopped = []
    p.until(lambda: stopped.append(1) if p.e.r32(STOP) and p.frames() < N else p.frames() >= N, SECONDS + 1)
    check([t for t in hit if t != 1], f'no-track ISO: the thread reached it ({hit})')
    check(not stopped and p.frames() == N, f'no-track ISO: a whole, {p.frames()} frames, stop flag clear {not stopped}')


def xfade_bad_next():
    """Crossfade holds a's tail back, and a's whole length comes out of the decoder before the report."""
    p = Player()
    (p.e.tmp/'tmp_q2xfade').write_bytes(struct.pack('<i', 2000))
    p.song('a.flac')
    p.run(0.5)
    p.eq()
    p.run(0.5)
    p.gapless('junk.flac')
    said = bytearray()
    def reported():
        said.extend(b''.join(p.e.replies))
        p.e.replies.clear()
        p.e.send_cmd('{player-status\\request}')
        return b'error' in said
    check(p.until(reported, SECONDS + 1), f'under Crossfade: reported ({p.frames()} of {N} frames out, the rest held: a limit)')
    check(not p.e.r32(STOP) and p.frames() >= N - 2 * RATE - 4096, f'under Crossfade: only the hold is missing, {p.frames()} of {N} frames')


def long_source(name):
    return array.array('h', (aud/f'{name}.wav').read_bytes()[-LONG_N * 4:])


def faded(fade):
    p = Player()
    (p.e.tmp/'tmp_q2xfade').write_bytes(struct.pack('<i', fade * 1000))
    p.song('long_a.flac')
    p.run(0.5)
    p.e.send_cmd('{mcl-seteq\\0|0|0|0|0|0|0|0|0|0|1}')
    p.e.send_cmd('{media-info\\get-music-info}')
    return p


def xfade_late_next(label, delay, again, wanted):
    """A song before a bad file holds its tail; the toast, then C. The tail goes under C up to XF_COLD_NS later, not after."""
    fade = 3
    p = faded(fade)
    p.run(3.5)
    p.gapless('junk.flac')
    said = bytearray()
    def reported():
        said.extend(b''.join(p.e.replies))
        p.e.replies.clear()
        p.e.send_cmd('{player-status\\request}')
        return b'error' in said
    check(p.until(reported, 12), f'{label}: reported')
    before = p.frames()
    if again:  # the second attempt demo may make under a second toast
        p.run(1)
        p.song('junk.flac')
        p.run(delay - 1)
    else:
        p.run(delay)
    base = p.frames()
    check(base == before == LONG_N - fade * RATE, f'{label}: only the hold is held back, {base} frames')
    p.song('long_c.flac')
    p.until(lambda: p.frames() >= base + 4 * RATE, 8)
    out, tail = array.array('h', bytes(p.pcm[base * 4:(base + RATE) * 4])), long_source('long_a')[-fade * RATE * 2:][:2 * RATE]
    share = sum(x * y for x, y in zip(out, tail)) / sum(y * y for y in tail)
    check((share > 0.9) == wanted and (share < 0.2) != wanted, f'{label}: the tail in C\'s first second, {share:.3f}')


def fade_edit(label, start, edit_at, ms, want_lo, want_hi):
    """The flag moves mid-track, as a Play Next moves it, replacing a preload as demo does: where does the song stop being
    the output, in seconds before its end? (None: never, a plain join.)"""
    p = Player()
    flag = p.e.tmp/'tmp_q2xfade'
    maps, real = [], p.e._mmap
    def spy(addr, length, prot, flags, fd, off):
        r = real(addr, length, prot, flags, fd, off)
        if 'q2xfade' in str(p.e.fds.get(fd, {}).get('path', b'')): maps.append(r)
        return r
    p.e._mmap = spy
    flag.write_bytes(struct.pack('<i', start))
    p.song('long_a.flac')
    t0 = p.now()
    p.run(0.5)
    p.e.send_cmd('{mcl-seteq\\0|0|0|0|0|0|0|0|0|0|1}')
    p.e.send_cmd('{media-info\\get-music-info}')
    events = [(5.0, 'preload')] + ([(edit_at, 'edit')] if edit_at is not None else [])
    name, loaded = 'long_b', False
    for at, what in sorted(events):
        p.run(max(0, t0 + at - p.now()))
        if what == 'preload':
            p.gapless(name + '.flac')
            loaded = True
        else:
            flag.write_bytes(struct.pack('<i', ms))
            for a in maps: p.e.w32(a, ms)
            name = 'long_c'
            if loaded:
                p.e.send_cmd('{mcl-closegapless\\null}')
                p.run(0.05)
                p.gapless(name + '.flac')
    p.until(lambda: p.frames() >= LONG_N + 2 * RATE, 10)
    out, a = array.array('h', bytes(p.pcm)), long_source('long_a')
    at = next((j // 2 for j in range(0, min(len(out), len(a)), 2) if abs(out[j] - a[j]) > 16), None)
    gone = None if at is None else (LONG_N - at) / RATE
    ok = gone is None if want_lo is None else gone is not None and want_lo <= gone <= want_hi
    check(ok, f'{label}: {gone} s before its end, wanted {want_lo} to {want_hi}')


def tail_noise(name, most):
    """Junk after the audio, against the same file without it: no more than most s of it comes out."""
    runs = []
    for f in name, name.removeprefix('junk'):
        p = Player()
        p.song(f)
        p.settled(SECONDS + 15)
        runs.append(bytes(p.pcm))
    width = 8 if len(runs[1]) > 6 * RATE * 4 else 4  # lavc's float decoders come out as 32 bits
    past = (len(runs[0]) - len(runs[1])) / width / RATE
    peak = max(map(abs, array.array('h', runs[0][len(runs[1]):])), default=0)
    print(f'     {name}: {past:.2f} s past the file without it, peak {peak}')
    check(runs[0][:len(runs[1]) - RATE * width // 2] == runs[1][:-RATE * width // 2], f'{name}: the audio itself comes out')
    if most is not None: check(past < most, f'{name}: {past:.2f} s of noise, under {most} s')


def stop_while_open():
    """demo's direct stop while the thread opens a good next: the thread goes stale, so what it was opening isn't played."""
    p = Player()
    p.e.hooks['delay'] = lambda raw: 1.0 if raw.endswith(b'b.flac') else 0
    p.song('a.flac')
    p.run(1)
    p.gapless('b.flac')
    p.run(0.2)
    p.e.send_cmd('{mcl-stop\\null}')
    p.run(0.1)
    check(p.e.r32(NEXT) == 3, f'stop in an open: the thread is stale ({p.e.r32(NEXT):#x})')
    p.run(1.5)
    check(p.fds('b.flac') == 0 and p.e.r32(NEXT) == 0, f"stop in an open: closed, state clear ({p.fds('b.flac')} open, {p.e.r32(NEXT):#x})")


def understated_mp3(name):
    """A frame count short of the audio plays out: what follows is frames."""
    p = Player()
    p.song(name)
    p.settled(SECONDS + 3)
    check(p.frames() >= N - 2304, f'{name}: plays to its end, {p.frames()} of {N} frames')


def past_the_count():
    """Past the count: frames joined on play, whatever follows them or a real tag's cover doesn't."""
    for name, audio, least, most in ('joined.mp3', 2 * N, -0.1, 0.1), ('junkjoined.mp3', 2 * N, -0.4, 1), ('apex.mp3', N, -0.1, 1):
        p = Player()
        p.song(name)
        p.settled(2 * SECONDS + 15)
        past = (p.frames() - audio) / RATE
        check(least < past < most, f'{name}: {p.frames()} frames, {audio} of audio, {past:.2f} s over, from {least} to {most} s')


def dsd():
    """DSD: plays, an understated header cuts nothing while something's still wanted, and the mute past the length is seen."""
    for name in 'whole.dsf', 'under.dsf':
        p = Player()
        muted = []
        p.e.watch_fn(0x43b280, lambda e: muted.append((round(p.now(), 2), p.frames(), e.u.reg_read(UC_MIPS_REG_A0))))
        p.song(name)
        p.settled(SECONDS + 3)
        print(f'     {name}: {p.frames()} frames at the end; mute helper calls (t, frames, arg) {muted[:6]}')
        if name == 'whole.dsf': whole = p.frames()
        else: check(p.frames() >= whole - 8192, f'{name}: as long as the whole one ({p.frames()} of {whole})')
    check(whole > 4 * RATE, f'DSD plays, {whole} frames')
    p = Player()  # past its length, where stock mutes it: stock's store ends it, not the file's end with the clock running
    p.song('whole.dsf')
    p.run(1)
    p.e.w32(0xb10e84, 2)
    p.settled(SECONDS + 3)
    print(f'     whole.dsf, a length of 2 s: {p.frames()} frames')
    check(p.frames() < whole * 5 // 8, f'DSD past its length ends, {p.frames()} of {whole}')


def seek_flac(name, rate, frames, block):
    """Bit-exact from the first frame at or past each target to the file's last sample, either side of the old threshold
    (last frame x its size / rate), right at the end, and seeking back."""
    src = (aud/f'{name}.wav').read_bytes()[-frames * 4:]
    last = frames // block
    short = frames - last * block
    edge = last * short // rate
    secs = frames // rate
    for targets in dict.fromkeys(((secs // 2,), (edge - 1,), (edge + 1,), (secs - 2,), (secs - 1,), (secs,), (min(edge + 1, secs - 2), max(edge - 3, 1)))):
        p = Player()
        p.song(f'{name}.flac')
        p.run(1)
        for t in targets:
            at = p.seek_drop(t)
            p.run(0.5)
        p.settled(secs + 3)
        land = min(math.ceil(targets[-1] * rate / block), last) * block
        out = bytes(p.pcm[at * 4:])
        check(out == src[land * 4:], f'{name} (edge {edge} s), seek {targets}: {len(out) // 4} frames from {land}, wanted {frames - land}; exact {out == src[land * 4:]}, end there {out[-4000:] == src[-4000:]}')


def seek_next():
    """A seek past the old threshold, then the next file: the whole end, the next song's first frame after it, a preload still."""
    name, rate, frames, block = SEEKS[0]
    src, nxt = (aud/f'{name}.wav').read_bytes()[-frames * 4:], (aud/'nextq.wav').read_bytes()[-8 * RATE * 4:]
    p = Player()
    p.song(f'{name}.flac')
    p.run(1)
    at = p.seek_drop(16)
    p.run(0.5)
    p.gapless('nextq.flac')
    p.until(lambda: p.frames() > at + (frames - 16 * rate) + RATE, 8)
    check(src[-RATE * 4:] + nxt[:RATE // 2 * 4] in bytes(p.pcm), 'seek, then next: the end, then its first half second, gapless')


def seek_past_end():
    """Past the file's length: it ends at once, and nothing's left playing."""
    name, rate, frames, block = SEEKS[0]
    p = Player()
    p.song(f'{name}.flac')
    p.run(1)
    at = p.seek_drop(frames // rate + 5)
    p.settled(4)
    check(p.frames() - at <= block and not p.fds(f'{name}.flac'), f'seek past the end: {p.frames() - at} frames, closed')


def seek_filtered(xfade):
    """With a live filter, flat: as long as without it and within rounding, and Crossfade neither loses the end."""
    name, rate, frames, block = SEEKS[0]
    src = array.array('h', (aud/f'{name}.wav').read_bytes()[-frames * 4:])
    p = Player()
    if xfade: (p.e.tmp/'tmp_q2xfade').write_bytes(struct.pack('<i', xfade * 1000))
    p.song(f'{name}.flac')
    p.run(0.5)
    p.eq()
    p.run(0.5)
    at = p.seek_drop(16)
    land = math.ceil(16 * rate / block) * block
    if xfade: p.gapless('nextq.flac')
    p.settled(25 if xfade else 8)
    out = array.array('h', bytes(p.pcm[at * 4:]))
    if xfade:  # the fade takes the seam's seconds off the two lengths, and the last song's own tail is held as well
        want = frames - land + 8 * RATE - 2 * xfade * RATE
        check(abs(len(out) // 2 - want) < 2048, f'seek under Crossfade: {len(out) // 2} frames, wanted {want} (+-2048)')
    else:
        want = src[land * 2:]
        off = max((abs(x - y) for x, y in zip(out, want)), default=99)
        check(len(out) == len(want) and off <= 2, f'seek, filter live: {len(out) // 2} of {len(want) // 2} frames, off by at most {off}')


def seek_other(name):
    """Lossy and other lossless: the last 0.2 s, as a run with no seek gives it, after a seek near the end and one back."""
    r = Player()
    r.song(name)
    r.settled(11)
    ref = bytes(r.pcm)
    width = 8 if len(ref) > 12 * RATE * 4 else 4
    K = RATE // 5 * width
    for targets in ((6,), (6, 2)):
        p = Player()
        p.song(name)
        p.run(1)
        for t in targets:
            at = p.seek_drop(t)
            p.run(0.3)
        p.settled(11)
        out = bytes(p.pcm[at * 4:])
        secs = len(out) / width / RATE
        check(out[-K:] == ref[-K:] and abs(secs - (8 - targets[-1])) < 1.5, f'{name}, seek {targets}: {secs:.2f} s out, its end there {out[-K:] == ref[-K:]}')


if __name__ == '__main__':
    failures = []
    setup()
    seam()
    bad_next('junk.flac')
    bad_next('empty.flac')
    bad_next('junk.dff')
    bad_next('junk.iso')
    bad_next('big.flac')
    bad_next('big.aac', 0x480184)
    bad_next('nostreams.wma', 0x4826a4)
    iso_no_tracks()
    exit_apart()
    metadata_cut()
    music_info_no_stream()
    eq_enable_no_stream()
    next_whole('x.wma')
    next_whole('bigmeta.flac')
    xfade_bad_next()
    stop_in_failed_open()
    stop_while_open()
    understated()
    late(1.5)
    late(0)
    late_failed()
    second_cold()
    race_publish()
    race_cold()
    close_while_open(False)
    close_while_open(True)
    close_while_open('race')
    for name, most in (('junkx.mp3', 1), ('junkx.m4a', 0.1), ('junkx.ogg', 0.1), ('junkx.wv', 0.1), ('junkx.flac', 0.1), ('junkx.wav', 0.1), ('junkx.wma', 0.1)):
        tail_noise(name, most)
    tail_noise('junkh.mp3', None)  # no header to say where the audio ends: a limit, docs/internals.md#forced-eof
    understated_mp3('off.mp3')
    understated_mp3('cut.mp3')
    understated_mp3('x.mp3')
    past_the_count()
    dsd()
    for case in SEEKS:
        seek_flac(*case)
    seek_flac('s44m', 44100, 215 * 4096 + 3000, 4096)
    seek_next()
    seek_past_end()
    seek_filtered(0)
    seek_filtered(2)
    for name in 'l.mp3', 'l.m4a', 'la.m4a', 'l.ogg', 'l.wv', 'l.wav':
        seek_other(name)
    for label, delay, again, wanted in (('late next, toast 2 s', 2.0, 0, True), ('late next, toast 5 s', 5.0, 0, True),
                                        ('late next, second toast', 5.0, 1, True), ('late next, 35 s', 35.0, 0, False)):
        xfade_late_next(label, delay, again, wanted)
    fade_edit('no fade', 0, None, 0, None, None)
    fade_edit('asked 1 s', 1000, None, 0, 0.95, 1.05)
    fade_edit('raised before the window', 0, 2.0, 1000, 0.95, 1.05)
    fade_edit('raised in the window', 0, 8.0, 1000, 0.3, 0.9)  # half of what is left
    fade_edit('raised after its last block', 0, 9.8, 1000, None, None)
    fade_edit('cleared before the window', 1000, 2.0, 0, None, None)
    fade_edit('cleared in the hold', 1000, 7.0, 0, 0.95, 1.05)  # a hold once begun is finished
    assert not failures, f'{len(failures)} failed: {failures}'
    shutil.rmtree(work)
    print('hci_natural: all passed')
