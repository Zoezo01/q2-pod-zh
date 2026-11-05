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
LONG = {'f44.flac': 5.5, 'g44.flac': 3, 'h48.flac': 3}  # Crossfade: its hold opens 2 x fade + 2 s before the end


def setup():
    
    shutil.rmtree(work, ignore_errors=True)
    aud.mkdir(parents=True)
    subprocess.run(['unsquashfs', '-no-progress', '-d', root, '-f', build/'stock.squashfs', 'lib', 'usr/lib'], check=True, stdout=subprocess.DEVNULL)
    for name, (rate, ch, fmt, codec) in FILES.items():
        depth = ['-sample_fmt', fmt, '-bits_per_raw_sample', '24'] if fmt == 's32' else ['-sample_fmt', fmt] if codec == 'flac' else []
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'sine=f={TONE}:r={rate}:d={LONG.get(name, SECONDS)}', '-af', 'volume=5.66', '-ac', str(ch),
                        *depth, '-c:a', codec, aud/name], check=True)


class Ring(PCM):
    """snd_pcm_avail's sums: hw_ptr runs with the clock once started and never stops by itself."""
    def __init__(self, os_):
        super().__init__(os_)
        self.hw_at, self.appl, self.lost, self.forwards, self.chunks = None, 0, 0, [], []
    @property
    def buffer_frames(self):
        return 16384 if self.rate >= 96000 else self.rate * self.buffer_us // 1000000
    def hw(self):
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
    def _alsa(self, n):
        a, p = self.a, self.pcm
        if n == 'snd_pcm_open':
            r = super()._alsa(n)
            self.pcm.__class__ = Ring
            Ring.__init__(self.pcm, self)
            self.streams.append(self.pcm)
            return r
        if n == 'snd_pcm_status':
            self.w32(a(1), p.state, 0)
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
            p.state, p.hw_at, p.appl = 2, None, 0
            return 0
        if n == 'snd_pcm_drop':
            p.state, p.hw_at = 1, None
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
    def __init__(self, way=0):
        KernelEmu.streams = []
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
    def song(self, name):
        self.e.send_cmd('{set-songurl\\/mnt/mmc/%s|ss=0|0|0|1|0|90}' % name)
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


signal.alarm(600)  # a spinning player fails the run
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
for way, route in ((0, 'cold'), (2, 'preload')):
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
def fade(second, excess):
    p = Player()
    (p.e.tmp/'tmp_q2xfade').write_bytes(struct.pack('<i', 1000))
    mixes, seen = [0], ps['xf.3']  # xf's members split: total is the fourth
    def watch(e):
        mixes[0] = max(mixes[0], e.r32(seen))
    p.e.watch_fn(ps['ao_write'], watch)
    p.excess = excess
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
# Across a rate: a reopen, drained, at the new rate, and the tail still mixes after it.
p, total = fade('h48.flac', None)
assert len(p.e.streams) == 2 and p.e.pcm.rate == 48000 and p.e.pcm.lost == 0 and total > 40000, (len(p.e.streams), p.e.pcm.rate, total)
print('Crossfade in Shuffle: at the same format one PCM and the whole tail mixed, past a dry first write too; across a rate one reopen and the tail mixed after it.')
shutil.rmtree(work, ignore_errors=True)
