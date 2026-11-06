#!/usr/bin/env python3
"""A natural change in the real hciplayer (hci_emu.py) against a PCM that runs as the kernel's does: stop_threshold and
silence_size at the boundary, so a dry PCM stays RUNNING on silence with hw_ptr past appl_ptr, and frames written behind
hw_ptr are lost. What is heard is read off the ring's timeline.

With no preload and demo's mark, a next song in the same format keeps the PCM, and any other reopens at what its file
is: never a tone at the wrong rate or a 24-bit file through a 16-bit PCM. A kept PCM that runs dry before the first
write moves past hw_ptr (ao_write), on the jack and USB: a short gap, no lost frame.
Usage: test/hci_ao.py BUILD_DIR [PLAYER]  (PLAYER: another hciplayer, to see old code fail)"""
import array, math, pathlib, shutil, signal, struct, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'tools'))  # before test/build.py
from build import symbols
from hci_emu import Emu, PCM

build = pathlib.Path(sys.argv[1]).resolve()
player = pathlib.Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else build/'peq'/'hciplayer'
work, root, aud = build/'hci_ao', build/'hci_ao'/'root', build/'hci_ao'/'aud'
AUTH, WAY = 0x43084c, 0xb10e54
ps = symbols(build/'peq'/'peq.elf')
TONE, SECONDS = 660, 1.5
FILES = {'a44.flac': (44100, 2, 's16', 'flac'), 'b44.flac': (44100, 2, 's16', 'flac'), 'b48.flac': (48000, 2, 's16', 'flac'),
         'c96.flac': (96000, 2, 's32', 'flac'), 'd44.flac': (44100, 1, 's16', 'flac'), 'a44.mp3': (44100, 2, 's16', 'libmp3lame'),
         'b44.mp3': (44100, 2, 's16', 'libmp3lame'), 'a192.flac': (192000, 2, 's32', 'flac'), 'b192.flac': (192000, 2, 's32', 'flac'),
         'f44.flac': (44100, 2, 's16', 'flac'), 'g44.flac': (44100, 2, 's16', 'flac'), 'h48.flac': (48000, 2, 's16', 'flac')}
FILES.update({'t100.flac': (44100, 2, 's16', 'flac'), 't400.flac': (44100, 2, 's16', 'flac')})
NOISE = {'n1.flac': ['-c:a', 'flac'], 'n2.flac': ['-c:a', 'flac'], 'n5.flac': ['-c:a', 'flac'], 'n6.flac': ['-c:a', 'flac'],
         'c1.mp3': ['-c:a', 'libmp3lame', '-q:a', '2'], 'c2.mp3': ['-c:a', 'libmp3lame', '-q:a', '2'], 'c1.m4a': ['-c:a', 'aac'], 'c2.m4a': ['-c:a', 'aac'],
         'c1.opus': ['-c:a', 'libopus'], 'c2.opus': ['-c:a', 'libopus'], 'c1.ogg': ['-c:a', 'libvorbis'], 'c2.ogg': ['-c:a', 'libvorbis']}
NOISE_SECONDS = {'n1.flac': 1.5, 'n2.flac': 3, 'n5.flac': 5.5, 'n6.flac': 3}
LONG = {'f44.flac': 5.5, 'g44.flac': 3, 'h48.flac': 3, 't100.flac': 0.1, 't400.flac': 0.4}  # Crossfade: its hold opens 2 x fade + 2 s before the end


def setup():
    
    shutil.rmtree(work, ignore_errors=True)
    aud.mkdir(parents=True)
    subprocess.run(['unsquashfs', '-no-progress', '-d', root, '-f', build/'stock.squashfs', 'lib', 'usr/lib'], check=True, stdout=subprocess.DEVNULL)
    for name, (rate, ch, fmt, codec) in FILES.items():
        depth = ['-sample_fmt', fmt, '-bits_per_raw_sample', '24'] if fmt == 's32' else ['-sample_fmt', fmt] if codec == 'flac' else []
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'sine=f={TONE}:r={rate}:d={LONG.get(name, SECONDS)}', '-af', 'volume=5.66', '-ac', str(ch),
                        *depth, '-c:a', codec, aud/name], check=True)
    for seed, (name, args) in enumerate(NOISE.items(), 1):  # pink noise: every frame its own, and the seam's level shows
        rate = 48000 if name.endswith('.opus') else 44100
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'anoisesrc=d={NOISE_SECONDS.get(name, 2)}:c=pink:r={rate}:seed={seed}:a=0.4', '-ac', '2',
                        *args, aud/name], check=True)
    body = bytes([0x69]) * (4096 * 2 * 138)  # DSD, 1 s (test/hci_natural.py)
    (aud/'z.dsf').write_bytes(b'DSD ' + struct.pack('<QQQ', 28, 92 + len(body), 0) + b'fmt ' + struct.pack('<QIIIIIIQI', 52, 1, 0, 2, 2, 2822400, 1, 138 * 4096 * 8, 4096) +
                              bytes(4) + b'data' + struct.pack('<Q', 12 + len(body)) + body)
    shutil.copy(aud/'z.dsf', aud/'y.dsf')


class Ring(PCM):
    """snd_pcm_avail's sums: hw_ptr runs with the clock once started and never stops by itself."""
    def __init__(self, os_):
        super().__init__(os_)
        self.hw_at, self.appl, self.lost, self.forwards, self.chunks = None, 0, 0, [], []
        self.paused, self.trigger, self.released, self.epochs = None, 0, 0, []  # a pause's hw_ptr and time; what a prepare set aside
    @property
    def buffer_frames(self):
        return 16384 if self.rate >= 96000 and not self.os.transport else self.rate * self.buffer_us // 1000000  # the DMA's cap isn't a transport's
    def hw(self):
        if self.paused is not None: return self.paused
        return 0 if self.hw_at is None else (self.os.clock - self.hw_at) * self.rate // 10**9
    def avail(self):
        return self.hw() + self.buffer_frames - self.appl
    def bytes(self):
        return {2: 2, 10: 4, 14: 4, 6: 3}.get(self.fmt, 4) * self.ch
    def samples(self):
        """Channel 0 of what got ahead of hw_ptr, on the timeline; a hole reads as silence."""
        width, out, end = self.bytes() // self.ch, array.array('f'), 0
        for at, data in self.chunks:
            out.extend([0.0] * (at - end))
            kind, scale = ('h', 32768.0) if width == 2 else ('i', 2147483648.0)
            values = array.array(kind, data[:len(data) // width * width])
            out.extend(v / scale for v in values[::self.ch])
            end = at + len(data) // self.bytes()
        return out


class KernelEmu(Emu):
    transport = None  # what a Bluetooth transport's one rate answers, whatever's asked
    def _alsa(self, n):
        a, p = self.a, self.pcm
        if n == 'snd_pcm_hw_params_set_rate_near' and self.transport and p:
            p.rate = self.transport
            self.w32(a(2), p.rate)
            return 0
        if n == 'snd_pcm_open':
            r = super()._alsa(n)
            self.pcm.__class__ = Ring
            Ring.__init__(self.pcm, self)
            self.streams.append(self.pcm)
            return r
        if n == 'snd_pcm_status':  # dac_resume's words too: state, trigger, tstamp, appl_ptr, hw_ptr
            self.w32(a(1), p.state, p.trigger // 10**9, p.trigger % 10**9, 0, 0, p.appl, p.hw())
            return 0
        if n in ('snd_pcm_status_get_avail', 'snd_pcm_avail_update', 'snd_pcm_avail'):
            self.reads += n == 'snd_pcm_status_get_avail'
            return p.avail()
        if n == 'snd_pcm_status_get_delay' or n == 'snd_pcm_delay':
            d = p.appl - p.hw()
            if n == 'snd_pcm_delay': self.w32(a(1), d); return 0
            return d
        if n == 'snd_pcm_status_get_state' or n == 'snd_pcm_state': return p.state
        if n == 'snd_pcm_prepare':
            if p.chunks: p.epochs.append(p.chunks)
            p.state, p.hw_at, p.appl, p.chunks, p.paused = 2, None, 0, [], None
            return 0
        if n == 'snd_pcm_drop':
            p.state, p.hw_at, p.paused = 1, None, None
            return 0
        if n == 'snd_pcm_pause':
            if a(1):
                p.paused, p.trigger, p.state = p.hw(), self.clock, 6
            else:  # hw_ptr where it was: soc_aic's jump to the next wrap (docs/internals.md#unpause) isn't modelled
                p.hw_at, p.paused, p.state = self.clock - p.paused * 10**9 // p.rate, None, 3
                p.released += 1
            return 0
        if n == 'snd_pcm_drain':
            left = max(p.appl - p.hw(), 0)
            p.state = 1
            self.event('pcm_drain', left=left, total=p.appl)
            return super_drain(self, left, p)
        if n == 'snd_pcm_forward':  # forward_appl_ptr: at most avail
            frames = min(a(1), max(p.avail(), 0))
            p.appl += frames
            p.forwards.append(frames)
            return frames
        if n == 'snd_pcm_writei':
            return self._writei(a(1), a(2))
        return super()._alsa(n)
    def _writei(self, buf, frames):
        p = self.pcm
        room = p.avail()
        n = min(frames, room)
        if n <= 0:
            self.block_current(self.clock + 5 * 10**6, None, 'write', retry=True)
            return False
        data, hw = bytes(self.u.mem_read(buf, n * p.bytes())), p.hw()
        behind = max(hw - p.appl, 0)
        if behind and p.hw_at is not None:
            p.lost += min(behind, n)
            data = data[min(behind, n) * p.bytes():]
            p.chunks.append((p.appl + min(behind, n), data))
        else:
            p.chunks.append((p.appl, data))
        p.appl += n
        if p.state in (1, 2) and p.appl >= min(p.start_threshold, p.buffer_frames):
            p.state, p.hw_at = 3, self.clock
        return n


def super_drain(e, left, p):
    from unicorn.mips_const import UC_MIPS_REG_V0, UC_MIPS_REG_PC, UC_MIPS_REG_RA
    e.u.reg_write(UC_MIPS_REG_V0, 0)
    e.u.reg_write(UC_MIPS_REG_PC, e.u.reg_read(UC_MIPS_REG_RA))
    e.block_current(e.clock + left * 10**9 // p.rate, None, 'drain')
    return False


class Player:
    def __init__(self, way=0, transport=None):
        KernelEmu.streams = []
        KernelEmu.transport = transport
        KernelEmu.reads = 0
        self.e = e = KernelEmu(player, root, work, log=work/'player.log', fsmap={'/mnt/mmc': str(aud)})
        e.hook_fn(AUTH, lambda e: 0)
        for name in 'mnt_data_peq-active', 'tmp_q2xfade', 'tmp_q2press':
            (e.tmp/name).unlink(missing_ok=True)
        e.alsa_active = True
        self.excess, self.jumped, self.looks = None, 0, 0
        e.watch_fn(ps['ao_write'], self.first_write)
        self.run(0.3)
        e.w32(WAY, way)
    def first_write(self, e):
        self.looks += e.r32(ps['ao_stayed'])  # writes that would look: the first after a kept PCM, and only it
        if self.excess is not None and e.r32(ps['ao_stayed']):
            p = e.pcm
            e.clock = p.hw_at + (p.appl + self.excess) * 10**9 // p.rate
            self.jumped += 1
            self.excess = None
    def run(self, s):
        self.e.run(until=(self.e.clock - 10**12) / 1e9 + s)  # hci_emu's until: seconds from boot
    def until(self, done, limit):
        end = self.e.clock + int(limit * 10**9)
        while self.e.clock < end:
            if done(): return True
            self.run(0.02)
        return False
    def song(self, name, keep=1):
        self.e.send_cmd('{set-songurl\\/mnt/mmc/%s|ss=0|0|0|%d|0|90}' % (name, keep))
    def gapless(self, name):
        self.e.send_cmd('{set-gapless\\/mnt/mmc/%s|ss=0|0|0|1|90}' % name)
    def ended(self):
        return self.e.r32(ps['ao_natural']) != 0
    def mark(self):
        """demo's start_mute: it left its mute off for this start."""
        (self.e.tmp/'tmp_q2press').write_bytes(struct.pack('<I', (self.e.clock // 10**6 & 0xffffffff) & ~1))
    def cold(self, first, second, marked=True):
        self.song(first)
        assert self.until(self.ended, 8), first
        if marked: self.mark()
        self.song(second)
        assert self.until(lambda: self.e.pcm and self.e.pcm.appl > self.e.pcm.buffer_frames and len(self.e.streams) >= 1 and not self.ended(), 3), second
        self.run(SECONDS + 0.8)
    def preloaded(self, first, second):
        self.song(first)
        self.run(0.2)
        self.gapless(second)
        assert self.until(self.ended, 5), first
        self.run(SECONDS + 0.8)


def tone(samples, rate):
    """Frequency by rising zero crossings, level, and the worst stretch without a crossing, of the whole run after its start."""
    s = samples[rate // 10:]
    ups = [i for i in range(1, len(s)) if s[i - 1] < 0 <= s[i]]
    freq = (len(ups) - 1) * rate / (ups[-1] - ups[0])
    rms = math.sqrt(sum(v * v for v in s) / len(s))
    return freq, rms, max(abs(v) for v in s)


def written(chunks):
    return b''.join(data for _, data in chunks)


def natural_into(first, second, excess=None, xfade=0, way=0):
    """A plays out by itself, B's first write has gone: the player at that moment."""
    p = Player(way)
    if xfade: (p.e.tmp/'tmp_q2xfade').write_bytes(struct.pack('<i', xfade))
    p.song(first)
    p.run(0.3)
    if xfade: p.e.send_cmd('{mcl-seteq\\0|0|0|0|0|0|0|0|0|0|1}')
    assert p.until(p.ended, 8), first
    p.mark()
    p.excess, end = excess, p.e.clock + 3 * 10**9
    p.song(second)
    while not p.looks and p.e.clock < end: p.run(0.001)
    assert p.looks, second
    return p


def pause_resume(p, after, age_ms=330):
    """after s on, demo's Pause with its fade age_ms old (mute_stamp), then Play; returns what the PCM showed at the pause."""
    p.run(after)
    pcm = p.e.pcm
    (p.e.tmp/'tmp_q2mute').write_bytes(struct.pack('<I', (p.e.clock // 10**6 - age_ms) & 0xffffffff | 1))
    p.e.send_cmd('{mcl-pause\\null}')
    assert p.until(lambda: pcm.state == 6, 1), (pcm.state, p.e.r32(WAY), p.e.pcm is pcm, p.ended(), p.e.events[-6:])
    p.run(0.3)
    held = pcm.hw(), pcm.appl, list(pcm.chunks)
    p.e.send_cmd('{mcl-play\\null}')
    assert p.until(lambda: pcm.state == 3, 1), pcm.state
    return held


TRIM_MS = -29  # peq_player.c PS_TRIM_MS


def replayed_right(p, held, reference):
    """What dac_resume wrote: the ring's unplayed part and back to the fade, within what is held. It ends where the ring
    stood, and the frames after it carry on: nothing lost, nothing from the wrong place. Its first 4 ms rise from silence.
    Returns 'stock' or (frames written, unplayed, back, held)."""
    pcm, (hw, appl, chunks) = p.e.pcm, held
    size, all_written = pcm.bytes(), written(chunks)
    new_from = chunks[-1][0]  # the chunks after the last hole: there timeline and written bytes line up
    for (at, data), (nxt, _) in zip(reversed(chunks[:-1]), reversed(chunks[1:])):
        if at + len(data) // size != nxt: break
        new_from = at
    since = sum(len(d) for at, d in chunks if at >= new_from) // size
    left = appl - hw
    if left > since:  # the hole itself is still to play: nothing goes back, as stock's resume
        assert pcm.released == 1 and not pcm.epochs, (left, since, pcm.released)
        return 'stock'
    assert pcm.released == 0 and pcm.epochs == [chunks], (pcm.released, len(pcm.epochs))
    stamp = struct.unpack('<I', (p.e.tmp/'tmp_q2mute').read_bytes())[0]
    age = (pcm.trigger // 10**6 - stamp) & 0xffffffff
    back = (age + TRIM_MS) * pcm.rate // 1000
    n = left + min(back, since - left)
    after, fade = written(pcm.chunks), pcm.rate // 1000 * 4
    assert after[fade * size:n * size] == all_written[-n * size:][fade * size:], ('replay', n, left, back, since)
    if reference is not None:
        whole = all_written + after[n * size:]
        assert whole == reference[:len(whole)], 'a frame lost or moved'
    return n, left, back, since


signal.alarm(1500)  # a spinning player fails the run
# Reopens, with demo's mark: each at what its file is, the tone right and the level clean.
setup()
for second, rate, width in (('b48.flac', 48000, 2), ('c96.flac', 96000, 4), ('d44.flac', 44100, 2)):
    p = Player()
    p.cold('a44.flac', second)
    assert len(p.e.streams) == 2 and p.looks == 0, (second, len(p.e.streams), p.looks)
    pcm = p.e.pcm
    if second == 'd44.flac':
        assert pcm.ch == 1, pcm.ch
    else:
        assert pcm.rate == rate and pcm.bytes() // pcm.ch == width, (second, pcm.rate, pcm.fmt)
    s = pcm.samples()
    f, rms, peak = tone(s[len(s) // 4:], pcm.rate)
    assert abs(f - TONE) < 2 and abs(rms / peak - 0.707) < 0.02 and peak < 0.75, (second, f, rms, peak)  # noise would crest and reach 1
# The same format stays open and whole: one PCM, the tone at its pitch all through, no frame lost.
for first, second in (('a44.flac', 'b44.flac'), ('a192.flac', 'b192.flac')):
    p = Player()
    p.cold(first, second)
    pcm = p.e.pcm
    assert len(p.e.streams) == 1 and pcm.lost == 0 and not pcm.forwards and p.looks == 1, (first, len(p.e.streams), pcm.lost, pcm.forwards, p.looks)
    f, rms, peak = tone(pcm.samples(), pcm.rate)
    assert abs(f - TONE) < 2 and abs(rms / peak - 0.707) < 0.03, (first, f, rms, peak)
# No mark: demo muted as stock, so stock's reopen.
p = Player()
p.cold('a44.flac', 'b44.flac', marked=False)
assert len(p.e.streams) == 2
print('Natural reopen: 44.1/16 to 48/16, 96/24 and mono each open a PCM at what the file is, the tone right; the same format stays open and whole.')

# The first write finds hw_ptr past appl_ptr by this many ms: the review's left values, with T the first block's decode.
period = lambda pcm: pcm.buffer_frames // 16
for way, route in ((0, 'cold'), (2, 'preload'), (2, 'cold')):
    for first, second, excess_ms in (('a44.mp3', 'b44.mp3', 4.0), ('a44.mp3', 'b44.mp3', 19.0), ('a44.flac', 'b44.flac', 1.93), ('a192.flac', 'b192.flac', 0.78),
                                     ('a192.flac', 'b192.flac', 40.0), ('a44.flac', 'b44.flac', 5.53)):
        p = Player(way)
        rate = FILES[first][0]
        p.excess = int(excess_ms * rate / 1000)
        want = p.excess
        (p.cold if route == 'cold' else p.preloaded)(first, second)
        pcm = p.e.pcm
        assert p.jumped == 1 and len(p.e.streams) == 1, (way, first, excess_ms, p.jumped, len(p.e.streams))
        assert pcm.lost == 0, (way, first, excess_ms, pcm.lost)
        assert len(pcm.forwards) == 1 and want <= pcm.forwards[0] <= want + period(pcm) + rate // 1000, (way, first, pcm.forwards, want)
        s = pcm.samples()
        hole = run = 0
        for v in s:
            run = run + 1 if v == 0 else 0
            hole = max(hole, run)
        assert hole <= want + period(pcm) + rate // 500, (way, first, excess_ms, hole)
    # Not yet past: nothing moved, one read more than the check at init.
    p = Player(way)
    p.excess = -200
    (p.cold if route == 'cold' else p.preloaded)('a44.flac', 'b44.flac')
    assert p.e.pcm.lost == 0 and not p.e.pcm.forwards, way
print('Kept PCM run dry by the first write: on the jack and USB, at 44.1 and 192 kHz, FLAC and MP3, appl_ptr moves past hw_ptr; no frame lost, a gap of the deficit and a period.')
# Crossfade in stock Shuffle: the filter in the chain, a fade asked, the held tail mixed under the next file's first frames.
def fade(second, excess, way=0):
    p = Player(way)
    (p.e.tmp/'tmp_q2xfade').write_bytes(struct.pack('<i', 1000))
    mixes, seen = [0], ps['xf'] + 12  # xf's total
    def watch(e):
        mixes[0] = max(mixes[0], e.r32(seen))
    p.e.watch_fn(ps['ao_write'], watch)
    p.excess, end = excess, p.e.clock + 3 * 10**9
    p.song('f44.flac')
    p.run(0.3)
    p.e.send_cmd('{mcl-seteq\\0|0|0|0|0|0|0|0|0|0|1}')
    assert p.until(p.ended, 8)
    p.mark()
    p.song(second)
    p.run(4)
    return p, mixes[0]
p, total = fade('g44.flac', 96)  # 96 frames past, the review's Crossfade case
pcm = p.e.pcm
assert total > 0 and len(p.e.streams) == 1 and pcm.lost == 0 and len(pcm.forwards) == 1, (total, len(p.e.streams), pcm.lost, pcm.forwards)
assert total > 40000, total  # the 1 s tail, held to the first block, is the mix's whole length
p, total = fade('g44.flac', 96, 2)  # USB's dry first write takes the same forward
pcm = p.e.pcm
assert total > 40000 and len(p.e.streams) == 1 and pcm.lost == 0 and len(pcm.forwards) == 1, (total, len(p.e.streams), pcm.lost, pcm.forwards)
# Across a rate: a reopen, drained, at the new rate, and the tail still mixes after it.
p, total = fade('h48.flac', None)
assert len(p.e.streams) == 2 and p.e.pcm.rate == 48000 and p.e.pcm.lost == 0 and total > 40000, (len(p.e.streams), p.e.pcm.rate, total)
print('Crossfade in Shuffle: at the same format one PCM and the whole tail mixed, past a dry first write too; across a rate one reopen and the tail mixed after it.')
# Pause soon after a natural change: the kept frames against the PCM's timeline.
def reference(first, second, xfade=0, seconds=3):
    p = natural_into(first, second, None, xfade)
    p.run(seconds)
    return written(p.e.pcm.chunks)
plain = reference('n1.flac', 'n2.flac')
for after in 0.02, 0.1, 0.4, 1.0:  # the fade's start in the old track, then the new
    p = natural_into('n1.flac', 'n2.flac')
    held = pause_resume(p, after)
    p.run(2.5)
    got = replayed_right(p, held, plain)
    assert got != 'stock' and got[0] == got[1] + got[2], (after, got)  # the whole way back to the fade
# The first write forwarded past a dry ring: what goes back is the new track's only, whatever the fade's age, and the
# hole is never filled with the old track's frames. (A pause with the hole still to play: test/bt_resample.py.)
for after in 0.002, 0.04, 0.1, 0.4:
    p = natural_into('n1.flac', 'n2.flac', excess=88)
    held = pause_resume(p, after)
    p.run(2.5)
    pcm = p.e.pcm
    assert len(pcm.forwards) == 1 and len(p.e.streams) == 1 and pcm.lost == 0, (after, pcm.forwards, pcm.lost)
    got = replayed_right(p, held, None)
    assert got != 'stock', (after, got)
print('Pause after a natural change: from the fade across the seam, every frame right; after a forward, only the new track goes back.')
# Pause inside Crossfade's mix on the PCM that stayed open: the mix's frames go back as they were written.
mixed = reference('f44.flac', 'g44.flac', 1000)
p = natural_into('f44.flac', 'g44.flac', None, 1000)
held = pause_resume(p, 0.3)
assert p.e.r32(ps['xf'] + 12) > 40000, 'mixing'
p.run(2.5)
assert len(p.e.streams) == 1, len(p.e.streams)
assert replayed_right(p, held, mixed) != 'stock'
print('Pause inside the mix on a stayed-open PCM: the mix resumes where the fade began, nothing lost.')
# Bluetooth and USB: writes leave the kept frames empty, a kept PCM included, and a resume is stock's.
for way in 1, 2:
    p = Player(way)
    p.song('n1.flac')
    p.run(0.2)
    p.gapless('n2.flac')
    held = pause_resume(p, 1.5)  # n2 under way, on the PCM n1 opened
    p.run(0.3)
    pcm = p.e.pcm
    fill, ring = p.e.r32(ps['ps.1']), bytes(p.e.u.mem_read(ps['ps_ring'], 1 << 16))
    assert fill == 0 and not any(ring) and pcm.released == 1 and not pcm.epochs, (way, fill, pcm.released, len(pcm.epochs))
    assert len(p.e.streams) == 1, (way, len(p.e.streams))
print('Bluetooth and USB: a write keeps nothing beside the ring, a kept PCM too, and an unpause is stock\'s.')
# A track under the exit hold, then a natural reopen: one wait, the drain whole, and none at all for a track of 300 ms or more.
HOLD_AFTER = 0x4226dc  # past exit_hold's jal and its delay slot
def short_then_reopen(short):
    p = Player()
    enter, left = [], []
    p.e.watch_fn(ps['exit_hold_c'], lambda e: enter.append(e.clock))
    p.e.watch_fn(HOLD_AFTER, lambda e: left.append(e.clock))
    p.song('a44.flac')
    assert p.until(lambda: left, 8)
    p.mark()
    p.song(short)
    assert p.until(lambda: len(left) == 2, 3), short
    pcm = p.e.pcm
    middle = written(pcm.chunks)
    p.mark()
    p.song('b48.flac')
    p.run(SECONDS + 0.8)
    assert len(p.e.streams) == 2 and p.e.pcm.rate == 48000 and pcm.lost == 0 and not pcm.forwards, (short, len(p.e.streams), pcm.lost, pcm.forwards)
    drains = [k for _, n, k in p.e.events if n == 'pcm_drain']
    assert [d['left'] > 0 for d in drains] == [True, False], (short, drains)  # ao_init's, then stock's own on a drained PCM
    assert written(pcm.chunks) == middle, short  # what the first PCM ended with reached the drain untouched
    return (left[1] - enter[1]) / 1e9, (left[1] - left[0]) / 1e9, drains[0]['left']
hold, apart, _ = short_then_reopen('t100.flac')
assert 0 < hold <= 0.3 and apart >= 0.299, (hold, apart)
hold, apart, _ = short_then_reopen('t400.flac')
assert hold == 0 and apart >= 0.299, (hold, apart)
print('A track under the exit hold, then a reopen: its exit waits once, the drain runs whole; a 400 ms track waits for nothing.')
# The other trims on the stay-open route: the same frames as a preload's, whose seams test/player_seam.py and
# test/mp3_gapless_player.py measure in samples, so the seam is what it was and the level can't step.
for first, second in (('c1.mp3', 'c2.mp3'), ('c1.m4a', 'c2.m4a'), ('c1.opus', 'c2.opus'), ('c1.ogg', 'c2.ogg')):
    p = Player()
    p.preloaded(first, second)
    want = written(p.e.pcm.chunks)
    p = Player()
    p.cold(first, second)
    pcm = p.e.pcm
    assert len(p.e.streams) == 1 and not pcm.lost and not pcm.forwards, (first, len(p.e.streams), pcm.lost, pcm.forwards)
    got = written(pcm.chunks)
    assert got == want, (first, len(got), len(want))
print('MP3, AAC in M4A, Opus and Vorbis on the stay-open route: frame for frame what a preload gives.')
# Crossfade's hold on the stay-open route: the level through hold, mix and on stays flat.
def windows(v, rate, ms=500):  # pink noise swings over 2 dB by itself in 100 ms
    n = rate * ms // 1000
    return [math.sqrt(sum(x * x for x in v[i:i + n]) / n) for i in range(0, len(v) - n, n)]
p = natural_into('n5.flac', 'n6.flac', None, 1000)
assert p.e.r32(ps['xf'] + 12) > 40000, 'mixing'
p.run(3)
assert len(p.e.streams) == 1, len(p.e.streams)
levels = windows(p.e.pcm.samples(), 44100)
mid = sorted(levels)[len(levels) // 2]
assert mid > 0.01 and all(abs(20 * math.log10(max(x, 1e-6) / mid)) < 1.5 for x in levels[1:-1]), [round(x, 3) for x in levels]
print('Crossfade on the stay-open route: the level stays within 1.5 dB through the hold and the mix.')
# Bluetooth and USB, real chain, with a volume: way 1 writes zeros without one. What leaves the player is compared.
def heard(way, transport=None):
    p = Player(way, transport)
    for command in '{mcl-setvol\\100}', '{mcl-softvolflag\\1}':
        p.e.send_cmd(command)
    p.run(0.05)
    return p
ROUTES = (1, None), (1, 96000), (2, None)  # Bluetooth at AAC's and SBC's 48 kHz, at LDAC's 96 kHz; a USB DAC
def out_rate(way, transport, name):
    return transport or (48000 if way == 1 else FILES.get(name, (44100,))[0])
# The same format: one PCM and every frame as a preload writes it, the resampler's carried tail in its place and none
# at the next track's head beyond that.
for way, transport in ROUTES:
    for first, second in (('n1.flac', 'n2.flac'), ('a44.flac', 'b44.flac'), ('h48.flac', 'b48.flac')):
        p = heard(way, transport)
        p.preloaded(first, second)
        p.run(1.5)
        want = written(p.e.pcm.chunks)
        assert len(p.e.streams) == 1, (way, transport, first)
        p = heard(way, transport)
        p.cold(first, second, marked=False)
        p.run(1.5)
        pcm = p.e.pcm
        assert len(p.e.streams) == 1 and pcm.lost == 0 and not pcm.forwards, (way, transport, first, len(p.e.streams), pcm.lost, pcm.forwards)
        assert pcm.rate == out_rate(way, transport, first), (way, transport, first, pcm.rate)
        assert written(pcm.chunks) == want and any(want), (way, transport, first, len(written(pcm.chunks)), len(want))
        seconds = sum(NOISE_SECONDS.get(n) or LONG.get(n, SECONDS) for n in (first, second))
        assert abs(len(want) // pcm.bytes() - seconds * pcm.rate) < 64, (way, transport, first, len(want) // pcm.bytes())
        if first[0] != 'n':
            s = pcm.samples()
            f, rms, peak = tone(s, pcm.rate)
            assert abs(f - TONE) < 2 and abs(rms / peak - 0.707) < 0.03, (way, transport, first, f, rms, peak)
print('Bluetooth at 48 and 96 kHz and USB, with a volume, a plain cold change: one PCM, frame for frame a preload\'s, the tone right.')
# Another file rate on Bluetooth: the request is a constant, so the PCM stays and the chain's resampler changes. The
# pitch holds on both sides of the change.
for transport in (None, 96000):
    for first, second in (('a44.flac', 'b48.flac'), ('h48.flac', 'b44.flac')):
        p = heard(1, transport)
        p.cold(first, second, marked=False)
        p.run(1.5)
        pcm = p.e.pcm
        assert len(p.e.streams) == 1 and pcm.lost == 0, (transport, first, len(p.e.streams), pcm.lost)
        s, rate = pcm.samples(), pcm.rate
        change = int(LONG.get(first, SECONDS) * rate)
        frames = len(written(pcm.chunks)) // pcm.bytes()
        assert abs(frames - change - SECONDS * rate) < 64, (transport, first, frames)
        for part in s[:change], s[change:]:
            f, rms, peak = tone(part, rate)
            assert abs(f - TONE) < 2 and abs(rms / peak - 0.707) < 0.03, (transport, first, f, rms, peak)
print('Bluetooth, 44.1 and 48 kHz files either way round: one PCM, the pitch right on each side, at 48 and 96 kHz.')
# Gapless off, another bit depth and channels: a reopen; nothing of the old track lost.
for way, transport in ROUTES:
    for second, keep in (('b44.flac', 0), ('c96.flac', 1), ('d44.flac', 1)):
        p = heard(way, transport)
        p.song('a44.flac')
        assert p.until(p.ended, 8)
        p.song(second, keep)
        p.run(SECONDS + 0.8)
        assert len(p.e.streams) == 2, (way, transport, second, keep, len(p.e.streams))
        assert all(pcm.lost == 0 for pcm in p.e.streams), (way, transport, second)
        assert any(k['left'] > 0 for _, n, k in p.e.events if n == 'pcm_drain'), (way, transport, second)
        for pcm in p.e.streams:
            f, rms, peak = tone(pcm.samples(), pcm.rate)
            assert abs(f - TONE) < 2 and peak > 0.3 and abs(rms / peak - 0.707) < 0.03, (way, transport, second, f, rms, peak)
            frames = len(written(pcm.chunks)) // pcm.bytes()
            assert abs(frames - SECONDS * pcm.rate) < 64, (way, transport, second, frames)  # the resampler's few short of a file
print('Bluetooth and USB: Gapless off, another bit depth and channels reopen, with what the buffer held drained first, tone and level right on both sides.')
# Bluetooth with the next track late: under BT_STAY_MS of the ring left, stock's reopen and its Drain; more, it stays.
def late(ms_left, transport=None):
    p = heard(1, transport)
    p.song('a44.flac')
    assert p.until(p.ended, 8)
    pcm = p.e.pcm
    assert p.until(lambda: (pcm.appl - pcm.hw()) * 1000 // pcm.rate <= ms_left, 1), ms_left
    p.song('b44.flac')
    p.run(SECONDS + 0.8)
    return p
for transport in (None, 96000):
    p = late(300, transport)
    assert len(p.e.streams) == 1 and not [k for _, n, k in p.e.events if n == 'pcm_drain'], (transport, len(p.e.streams))
    p = late(120, transport)
    drains = [k['left'] for _, n, k in p.e.events if n == 'pcm_drain']
    assert len(p.e.streams) == 2 and drains and drains[0] > 0, (transport, len(p.e.streams), drains)
print('Bluetooth, the next track late: 300 ms left stays on one PCM; 120 ms left reopens through stock\'s Drain.')
# Crossfade in Shuffle: the whole tail mixed over one PCM, the level flat, and across a rate on Bluetooth the pitch right.
def fade_over(way, transport, first, second, preload=False):
    p = heard(way, transport)
    (p.e.tmp/'tmp_q2xfade').write_bytes(struct.pack('<i', 1000))
    total, seen = [0], ps['xf'] + 12
    p.e.watch_fn(ps['ao_write'], lambda e: total.__setitem__(0, max(total[0], e.r32(seen))))
    p.song(first)
    p.run(0.3)
    p.e.send_cmd('{mcl-seteq\\0|0|0|0|0|0|0|0|0|0|1}')
    if preload:
        p.gapless(second)
    assert p.until(p.ended, 8), first
    if not preload:
        p.song(second)
    p.run(4)
    assert total[0] > 40000, (way, transport, first, total)
    return p
def as_preload(pcm, other):
    """Every frame in its place. Not bytes: the mix's gains are exact at each block's start, and the routes cut blocks apart, a 16-bit LSB at most."""
    x, y = pcm.samples(), other.samples()
    return len(x) == len(y) and all(abs(v - w) < 2**-14 for v, w in zip(x, y))
def holes(s):
    run = worst = 0
    for v in s:
        run = run + 1 if v == 0 else 0
        worst = max(worst, run)
    return worst
for way, transport in ROUTES:
    p = fade_over(way, transport, 'n5.flac', 'n6.flac')
    pcm = p.e.pcm
    assert len(p.e.streams) == 1 and pcm.lost == 0 and not pcm.forwards, (way, transport, len(p.e.streams), pcm.lost, pcm.forwards)
    levels = windows(pcm.samples(), pcm.rate)
    mid = sorted(levels)[len(levels) // 2]
    assert mid > 0.01 and all(abs(20 * math.log10(max(x, 1e-6) / mid)) < 1.5 for x in levels[1:-1]), (way, transport, [round(x, 3) for x in levels])
    assert holes(pcm.samples()) < pcm.rate // 100, (way, transport, holes(pcm.samples()))
    assert as_preload(pcm, fade_over(way, transport, 'n5.flac', 'n6.flac', True).e.pcm), (way, transport)
for transport in (None, 96000):
    p = fade_over(1, transport, 'f44.flac', 'h48.flac')
    pcm = p.e.pcm
    assert len(p.e.streams) == 1 and pcm.lost == 0, (transport, len(p.e.streams), pcm.lost)
    f, rms, peak = tone(pcm.samples(), pcm.rate)
    assert abs(f - TONE) < 2 and holes(pcm.samples()) < pcm.rate // 100, (transport, f)
    assert as_preload(pcm, fade_over(1, transport, 'f44.flac', 'h48.flac', True).e.pcm), transport
print('Crossfade on Bluetooth at 48 and 96 kHz and on USB: one PCM, within 1.5 dB through the hold and the mix, the whole tail mixed; across a rate on Bluetooth the pitch holds.')
# A real DSD end stays stock's on either way.
for way in 1, 2:
    p = heard(way)
    p.song('z.dsf')
    ends = []
    p.e.watch_fn(ps['ao_uninit'], lambda e: ends.append(e.clock))
    assert p.until(lambda: ends, 8), way
    p.run(0.02)
    assert p.e.pcm.appl > 0 and p.e.r32(ps['ao_natural']) == 0, way
    p.song('b44.flac')
    p.run(SECONDS + 0.8)
    assert len(p.e.streams) == 2, (way, len(p.e.streams))
print('A DSD file\'s end on Bluetooth and USB: not taken as natural, and the next file reopens.')
# The jack's natural start, demo's mute and sleep skipped: the line lets go, then DAC_RELEASE_MS, then frames, once.
# The routes ao_init's branch doesn't take read the mark at init's end. Time from the release to hw_ptr's start.
def released(first, second, marked=True, preload=False):
    p = Player()
    ends, freed = [], []
    p.e.watch_fn(ps['ao_uninit'], lambda e: ends.append(e.clock))
    p.e.watch_fn(ps['dac_ioctl'], lambda e: freed.append(e.clock))
    p.song(first)
    if preload:
        p.run(0.2)
        p.gapless(second)
    assert p.until(lambda: ends, 8), first
    if not preload:
        p.run(0.02)
        if marked: p.mark()
        p.song(second)
    p.run(1)
    return (p.e.pcm.hw_at - freed[-1]) // 10**6, len(freed), len(p.e.streams)
for first, second in (('a44.flac', 'z.dsf'), ('z.dsf', 'y.dsf'), ('z.dsf', 'b44.flac'), ('a44.flac', 'b48.flac')):
    wait, opens, pcms = released(first, second)
    assert wait == 130 and opens == 2 and pcms == 2, (first, second, wait, opens, pcms)
# No mark: demo muted and slept as stock, so stock's timing. A preloaded change at another format waits once, the same.
wait, opens, pcms = released('a44.flac', 'z.dsf', marked=False)
assert wait == 0 and pcms == 2, (wait, pcms)
wait, opens, pcms = released('a44.flac', 'b48.flac', preload=True)
assert wait == 130 and pcms == 2, (wait, pcms)
# The same format stays open: nothing to release, no wait, and a preload the same.
for preload in (False, True):
    wait, opens, pcms = released('a44.flac', 'b44.flac', preload=preload)
    assert opens == 1 and pcms == 1, (preload, opens, pcms)
# A natural end out of DSD waits with no mark: a preload, where demo has no mute, and a cold one. The flag also reads 1 at
# a PCM track's end into a preloaded DSD one, so that waits too. Into DSD with no preload and no mark, and PCM to PCM, as before.
for first, second, preload in (('z.dsf', 'b44.flac', True), ('z.dsf', 'y.dsf', True), ('z.dsf', 'b44.flac', False), ('a44.flac', 'z.dsf', True)):
    wait, opens, pcms = released(first, second, marked=False, preload=preload)
    assert wait == 130 and opens == 2 and pcms == 2, (first, second, preload, wait, opens, pcms)
wait, opens, pcms = released('a44.flac', 'b48.flac', marked=False)
assert wait == 0 and pcms == 2, (wait, pcms)
# ao_uninit's own word, as stock's uninit starts: a natural end out of DSD sets it; the same end cut by a press, and PCM's end, don't.
def uninit_word(first, second, pressed):
    p = Player()
    words = []
    if pressed: p.e.watch_fn(ps['ao_uninit'], lambda e: e.w32(0xb10e6c, 1))
    p.e.watch_fn(0x43868c, lambda e: words.append(e.r32(ps['ao_wait'])))
    p.song(first)
    p.run(0.2)
    p.gapless(second)
    assert p.until(lambda: words, 8), (first, pressed)
    return words[0]
assert uninit_word('z.dsf', 'b44.flac', False) == 1
assert uninit_word('z.dsf', 'b44.flac', True) == 0
assert uninit_word('a44.flac', 'b48.flac', False) == 0
print('The jack, a natural start: DSD on either side and another format wait once before frames; a natural end out of DSD waits with no mark, a press-cut one sets no word; a stay-open adds none.')
shutil.rmtree(work, ignore_errors=True)
