#!/usr/bin/env python3
"""The seam between two tracks, in samples: the built hciplayer, with its real demuxers and decoders, in real MIPS.

Generated pairs play through a real preload (set-songurl, set-gapless, as demo sends them) and every PCM frame is
kept. A stretch of each source, found by correlation, gives where that track's first sample came out, so priming and
padding are counted, not guessed. ld.so, libc and the player are stock's; ALSA is a model whose ring drains on a virtual
clock, threads are green. Files come from ffmpeg and lame, HE-AAC from test/fixtures (libfdk-aac 2.0.4: 1 s of pink noise
at AOT 5, 48 kbps, and AOT 29, 32 kbps); patch/lavf_gapless.c's numbers are what's under test. Stock's player, from the
same squashfs, is what a file with numbers of no use must come out as.
Usage: test/player_seam.py BUILD_DIR [HCIPLAYER]   (a stock HCIPLAYER shows what each case measured before the fix)"""
import array, math, pathlib, shutil, struct, subprocess, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from hci_emu import Emu as HciEmu

AUTH = 0x43084c  # the player's licence check


class Emu(HciEmu):
    """Every PCM write kept as (rate, channels, format, bytes), and each drop's place among them."""
    def __init__(self, exe, root, aud, tmp):
        self.chunks, self.drops = [], []
        super().__init__(exe, root, pathlib.Path(tmp).parent, fsmap={'/mnt/mmc': str(aud)})
        self.hook_fn(AUTH, lambda e: 0)
        self.alsa_active = True
        self.on_write = lambda e, data, n, p: self.chunks.append((p.rate, p.ch, p.fmt, data))

    def event(self, name, **kw):
        if name == 'pcm_drop': self.drops.append(len(self.chunks))
        super().event(name, **kw)

    def run(self, seconds):
        return super().run(until=(self.clock - 10**12) / 1e9 + seconds)


SECONDS = 2
TEMPLATE_AT, TEMPLATE = 30000, 4096
SAMPLES = 44100 * SECONDS
PADDING = -(SAMPLES + 1024) % 1024  # what FFmpeg's AAC encoder pads these to
LONG = 4 * 44100
EDGE = SAMPLES + 29
CHAIN_LINKS = {96000: 192000, 48480: 144480, 113760: 209760}  # a first link's frames, and the pair's true total with B
SHORT = 50, 500, 8000, 22000


def ffmpeg(*args): subprocess.check_call(['ffmpeg', '-v', 'error', '-y', *map(str, args)])


def source(path, seed, rate, samples=None):
    """Exactly SECONDS of full-level noise, or samples, loud to its last sample so a cut shows."""
    samples = samples or rate * SECONDS
    ffmpeg('-f', 'lavfi', '-i', f'anoisesrc=d={samples // rate + 1}:c=pink:r={rate}:seed={seed}:a=0.4', '-ac', 2,
           '-af', f'atrim=end_sample={samples}', '-c:a', 'pcm_s16le', path)


def left(wav):
    b = pathlib.Path(wav).read_bytes()
    a = array.array('h', b[b.index(b'data') + 8:])
    return differences([x / 32768 for x in a[::2]])


def differences(x):
    """Whitened: pink noise's neighbours are nearly the same, and the correlation's peak should be one sample wide."""
    return [b - a for a, b in zip(x, x[1:])]


def find(out, signal, lo, hi):
    """Where signal[TEMPLATE_AT:] sits in out, as the offset of signal's first sample, and the match's quality."""
    t = signal[TEMPLATE_AT:TEMPLATE_AT + TEMPLATE]
    lo, hi = max(lo + TEMPLATE_AT, 0), min(hi + TEMPLATE_AT, len(out) - TEMPLATE)
    best, at = -1, lo
    for i in range(lo, hi):
        c = math.sumprod(out[i:i + TEMPLATE], t)
        if c > best: best, at = c, i
    energy = math.sqrt(math.sumprod(t, t) * math.sumprod(*[out[at:at + TEMPLATE]] * 2)) or 1
    return at - TEMPLATE_AT, best / energy


def insert_box(path, chain, atom):
    """Atom at the end of the box chain leads to, growing its parents; ffmpeg puts moov after mdat, so no chunk offset
    moves. chain: (name, header size) of each."""
    b = bytearray(pathlib.Path(path).read_bytes())
    def child(start, end, name):
        while start < end:
            size, = struct.unpack_from('>I', b, start)
            if b[start + 4:start + 8] == name: return start, start + size
            start += size
        raise ValueError(name)
    parents, lo, hi = [], 0, len(b)
    for name, skip in chain:
        lo, hi = child(lo, hi, name)
        parents.append(lo)
        lo += skip
    b[hi:hi] = atom
    for at in parents:
        struct.pack_into('>I', b, at, struct.unpack_from('>I', b, at)[0] + len(atom))
    pathlib.Path(path).write_bytes(b)


def add_tag(path, value):
    """An iTunSMPB freeform tag in moov/udta/meta/ilst, as iTunes and qaac write it."""
    def box(name, body): return struct.pack('>I', 8 + len(body)) + name + body
    insert_box(path, ((b'moov', 8), (b'udta', 8), (b'meta', 12), (b'ilst', 8)),
               box(b'----', box(b'mean', bytes(4) + b'com.apple.iTunes') + box(b'name', bytes(4) + b'iTunSMPB')
                   + box(b'data', struct.pack('>II', 1, 0) + value.encode())))


def poke(path, name, skip, fmt, *values):
    """Overwrites a field skip bytes after the first box of that name in moov."""
    b = bytearray(pathlib.Path(path).read_bytes())
    struct.pack_into(fmt, b, b.index(name, b.index(b'moov')) + 4 + skip, *values)
    pathlib.Path(path).write_bytes(b)


def set_stts(path, entries):
    at, old = stts(path)
    assert len(old) == len(entries)
    b = bytearray(pathlib.Path(path).read_bytes())
    for i, e in enumerate(entries): struct.pack_into('>II', b, at + 8 * i, *e)
    pathlib.Path(path).write_bytes(b)


def timescale(path, ticks, edit):
    """A movie timescale of ticks a second, and the edit's duration in them, as other muxers than FFmpeg's write."""
    poke(path, b'mvhd', 12, '>I', ticks)
    poke(path, b'elst', 8, '>I', edit)


def stts(path):
    """stts's entries as (their offset, [(count, delta), ...]); moov's only."""
    b = pathlib.Path(path).read_bytes()
    at = b.index(b'stts', b.index(b'moov')) + 8
    n, = struct.unpack_from('>I', b, at)
    return at + 4, [struct.unpack_from('>II', b, at + 4 + 8 * i) for i in range(n)]


def whole_frames(path):
    """The last stts entry as a whole frame, as encoders that leave the end to a tag or the edit list do. Returns the
    frames the file now declares."""
    at, entries = stts(path)
    b = bytearray(pathlib.Path(path).read_bytes())
    if entries[-1][1] != 1024:
        struct.pack_into('>II', b, at + 8 * (len(entries) - 1), 1, 1024)
    pathlib.Path(path).write_bytes(b)
    return sum(c for c, _ in entries) * 1024


def generate(d):
    for s, seed in ('a', 1), ('b', 2):
        source(d/f'{s}.wav', seed, 44100)
        source(d/f'{s}48.wav', seed, 48000)
        wav = d/f'{s}.wav'
        ffmpeg('-i', wav, '-c:a', 'flac', d/f'{s}.flac')
        ffmpeg('-i', wav, '-c:a', 'alac', d/f'{s}.alac.m4a')
        subprocess.check_call(['lame', '--quiet', '-q', '2', '-V', '2', wav, d/f'{s}.mp3'])
        aac = '-c:a', 'aac', '-b:a', '192k'
        ffmpeg('-i', wav, *aac, d/f'{s}.m4a')  # FFmpeg's: edit list, last stts entry short
        ffmpeg('-i', wav, *aac, '-f', 'adts', d/f'{s}.aac')
        ffmpeg('-i', wav, '-c:a', 'libvorbis', '-q:a', 5, d/f'{s}.ogg')
        ffmpeg('-i', d/f'{s}48.wav', '-c:a', 'libopus', '-b:a', '128k', d/f'{s}.opus')
        # An edit list and whole frames: the end is the edit's duration.
        shutil.copy(d/f'{s}.m4a', d/f'{s}_elst.m4a')
        whole_frames(d/f'{s}_elst.m4a')
        # No edit list, whole frames: iTunes' shape, or nothing to go on but a tag.
        ffmpeg('-i', wav, *aac, '-use_editlist', 0, '-metadata', 'title=x', d/f'{s}_plain.m4a')
        total = whole_frames(d/f'{s}_plain.m4a')
        padding = total - 1024 - SAMPLES
        def tagged(name, priming, remainder, length):
            shutil.copy(d/f'{s}_plain.m4a', d/f'{s}_{name}.m4a')
            add_tag(d/f'{s}_{name}.m4a', f' 00000000 {priming:08X} {remainder:08X} {length:016X} 00000000 00000000')
        tagged('tag', 1024, padding, SAMPLES)
        tagged('tag_remainder', 1024, padding, 0)  # the total left out: the remainder says it
        # The tag's priming over more than a frame, past real audio: 1088 are lost, and the end still counts from the tag.
        tagged('tag_2112', 2112, padding, SAMPLES - 1088)
        tagged('junk_total', 1024, padding, 0x7fffffff)  # longer than the file
        tagged('junk_edit', 1024, padding, SAMPLES // 2)  # a tag left behind by an edit: half the file would go
        tagged('junk_priming', 0xffff, padding, SAMPLES)  # lavf refuses it
    # Wrong ends: a track long enough that an end 2 s early is mid-track.
    source(d/'c.wav', 3, 44100, LONG)
    source(d/'d.wav', 4, 44100, LONG)
    aac = '-c:a', 'aac', '-b:a', '192k'
    vorbis = '-c:a', 'libvorbis', '-q:a', 5
    for s in 'c', 'd':
        ffmpeg('-i', d/f'{s}.wav', *aac, d/f'{s}.m4a')
    ffmpeg('-i', d/'c.wav', *aac, '-use_editlist', 0, d/'plain.m4a')
    whole_frames(d/'plain.m4a')
    (count, _), _ = stts(d/'plain.m4a')[1]
    for name, entries in ('stts_136', [(count - 100, 1024), (1, 136)]), ('stts_0', [(count - 100, 1024), (1, 0)]), \
            ('last_delta_0', [(count, 1024), (1, 0)]):
        shutil.copy(d/'plain.m4a', d/f'{name}.m4a')
        set_stts(d/f'{name}.m4a', entries)
    def wrong(name, priming, remainder, length):
        shutil.copy(d/'plain.m4a', d/f'{name}.m4a')
        add_tag(d/f'{name}.m4a', f' 00000000 {priming:08X} {remainder:08X} {length:016X} 00000000 00000000')
    padding = (count + 1) * 1024 - 1024 - LONG
    wrong('stale', 1024, padding, LONG - 5000)
    wrong('stale_near', 1024, padding, LONG - 400)
    wrong('stale_300', 1024, padding, LONG - 300)
    wrong('stale_frame', 1024, padding, LONG - (1024 - padding))  # a whole frame of padding: none is
    wrong('stale_100', 1024, padding, LONG - 100)  # inside the last packet
    wrong('priming0', 0, 0, LONG)
    wrong('priming0_small', 0, 8, 0)  # a little padding, as a tag might say on a file with real priming
    ffmpeg('-i', d/'c.wav', *aac, '-movflags', 'frag_keyframe+empty_moov', '-frag_duration', 500000, d/'fragmented_unseekable.m4a')
    shutil.copy(d/'fragmented_unseekable.m4a', d/'frag.m4a')
    # A last sample holding two AUs: the decoder puts the second back, half a packet read with none queued. Dropping
    # samples moves where in a block the first of them falls.
    for drop in range(4):
        f = d/f'two_aus_{drop}.m4a'
        shutil.copy(d/'plain.m4a', f)
        set_stts(f, [(count - 1 - drop, 1024), (1, 232)])
        b = bytearray(f.read_bytes())
        at = b.index(b'stsz', b.index(b'moov')) + 4
        n, = struct.unpack_from('>I', b, at + 8)
        sizes = struct.unpack_from(f'>{n}I', b, at + 12)
        struct.pack_into('>I', b, at + 8, n - 1 - drop)
        struct.pack_into('>I', b, at + 12 + 4 * (n - 2 - drop), sizes[-2 - drop] + sizes[-1 - drop])
        f.write_bytes(b)
    # Real HE-AAC and HE+PS, at the core's timescale and the output's, and the output's with stts in the core's and a tag.
    for he in 'he', 'ps':
        ffmpeg('-i', pathlib.Path(__file__).parent/'fixtures'/f'{he}.aac', '-c', 'copy', d/f'{he}.m4a')
        for shape, ticks, delta, tag in ('core', 22050, 1024, False), ('output', 44100, 2048, False), ('lying', 44100, 1024, True):
            f = d/f'{he}_{shape}.m4a'
            shutil.copy(d/f'{he}.m4a', f)
            poke(f, b'mdhd', 12, '>I', ticks)
            set_stts(f, [(c, delta) for c, _ in stts(f)[1]])
            if tag: add_tag(f, ' 00000000 00000400 00000020 0000000000000000 00000000 00000000')
    # Timestamps in anything but the output rate's units, on LC: an mdhd changed.
    shutil.copy(d/'plain.m4a', d/'timebase.m4a')
    poke(d/'timebase.m4a', b'mdhd', 12, '>I', 22050)
    shutil.copy(d/'a.m4a', d/'timebase_edit.m4a')
    poke(d/'timebase_edit.m4a', b'mdhd', 12, '>I', 22050)
    shutil.copy(d/'a.m4a', d/'timebase_high.m4a')  # priming below 0 by less than its length, in the wrong units
    poke(d/'timebase_high.m4a', b'mdhd', 12, '>I', 88200)
    # An edit list that's not ours to read: two entries (a short one first), or a media time past lavf's priming bound.
    shutil.copy(d/'a_elst.m4a', d/'two_edits.m4a')
    b = pathlib.Path(d/'two_edits.m4a').read_bytes()
    at = b.index(b'elst', b.index(b'moov')) + 8
    duration, media, rate = struct.unpack_from('>IiI', b, at + 4)
    poke(d/'two_edits.m4a', b'elst', 4, '>I', 2)
    poke(d/'two_edits.m4a', b'elst', 8, '>I', duration - 100)
    insert_box(d/'two_edits.m4a', ((b'moov', 8), (b'trak', 8), (b'edts', 8), (b'elst', 16)), struct.pack('>IiI', 100, media, rate))
    # An edit too far from the file to be its end: FFmpeg's stts still says it.
    for name, off in ('edit_short', -5000), ('edit_long', 5000):
        shutil.copy(d/'a.m4a', d/f'{name}.m4a')
        poke(d/f'{name}.m4a', b'elst', 8, '>I', duration + off)
    shutil.copy(d/'a_elst.m4a', d/'media_time_big.m4a')
    poke(d/'media_time_big.m4a', b'elst', 12, '>i', 20000)
    # A long priming from an edit: whole blocks before 0.
    shutil.copy(d/'a.m4a', d/'media_time_8192.m4a')
    poke(d/'media_time_8192.m4a', b'elst', 12, '>i', 8192)
    shutil.copy(d/'a.m4a', d/'media_time_16000.m4a')
    poke(d/'media_time_16000.m4a', b'elst', 12, '>i', 16000)
    # A tag and an edit list: the decoder skips the priming and the timestamps start below 0 as well.
    shutil.copy(d/'a.m4a', d/'tag_and_edit.m4a')
    add_tag(d/'tag_and_edit.m4a', f' 00000000 00000400 00000000 {SAMPLES:016X} 00000000 00000000')
    # Priming over a frame, from both: the decoder's skip leaves a part of a third packet, whose frames are still below 0.
    shutil.copy(d/'a.m4a', d/'tag_and_edit_2112.m4a')
    poke(d/'tag_and_edit_2112.m4a', b'elst', 12, '>i', 2112)
    add_tag(d/'tag_and_edit_2112.m4a', f' 00000000 00000840 00000000 {SAMPLES - 1088:016X} 00000000 00000000')
    # A tag's total far short on FFmpeg's file: the edit and stts still say the end.
    shutil.copy(d/'a.m4a', d/'tag_far_short.m4a')
    add_tag(d/'tag_far_short.m4a', f' 00000000 00000400 00000000 {SAMPLES - 5000:016X} 00000000 00000000')
    # Whole frames and a timescale that can't say where in a frame the music ends. EDGE isn't a whole number of ticks.
    source(d/'e.wav', 5, 44100, EDGE)
    ffmpeg('-i', d/'e.wav', *aac, d/'edge.m4a')
    whole_frames(d/'edge.m4a')
    for ticks, name in (1000, 'edge_1000'), (600, 'edge_600'):
        shutil.copy(d/'edge.m4a', d/f'{name}.m4a')
        timescale(d/f'{name}.m4a', ticks, EDGE * ticks // 44100)
    # Links: the last page's granule, the whole file's end to ogg_get_length, is the last link's, far too early.
    def link(name, wav, *args, encoder=vorbis):
        ffmpeg('-i', d/wav, *encoder, *args, d/f'{name}.tmp.ogg')
        return (d/f'{name}.tmp.ogg').read_bytes()
    (d/'chain.ogg').write_bytes(link('l1', 'c.wav', '-serial_offset', 7) + link('l2', 'b.wav', '-serial_offset', 7))
    (d/'chain_serials.ogg').write_bytes(link('l1', 'c.wav', '-serial_offset', 7) + link('l2', 'b.wav', '-serial_offset', 9))
    (d/'chain_rates.ogg').write_bytes(link('l1', 'c.wav', '-serial_offset', 7) + link('l2', 'b48.wav', '-serial_offset', 9))
    opus = '-c:a', 'libopus', '-b:a', '128k'
    (d/'chain.opus').write_bytes(link('l1', 'a48.wav', '-serial_offset', 7, encoder=opus) + link('l2', 'b48.wav', '-serial_offset', 9, encoder=opus))
    # Where one link's end falls in a block changes what a block holds of the next's start.
    for frames in CHAIN_LINKS:
        source(d/f'o{frames}.wav', 6, 48000, frames)
        first = link('l1', f'o{frames}.wav', '-serial_offset', 7, encoder=opus)
        (d/f'chain_{frames}.opus').write_bytes(first + link('l2', 'b48.wav', '-serial_offset', 7, encoder=opus))
    (d/'chain_three.opus').write_bytes(link('l1', 'o48480.wav', '-serial_offset', 7, encoder=opus)
                                       + link('l2', 'a48.wav', '-serial_offset', 7, encoder=opus) + link('l3', 'b48.wav', '-serial_offset', 7, encoder=opus))
    # Tracks under a second, all their audio on one page, and one cut without re-encoding.
    for frames in SHORT:
        source(d/f's{frames}.wav', 7, 44100, frames)
        ffmpeg('-i', d/f's{frames}.wav', *vorbis, d/f's{frames}.ogg')
    ffmpeg('-i', d/'s8000.wav', *aac, d/'s8000.m4a')
    source(d/'s48.wav', 7, 48000, 8000)
    ffmpeg('-i', d/'s48.wav', *opus, d/'s8000.opus')
    ffmpeg('-i', d/'a.ogg', '-t', 1, '-c', 'copy', d/'cut.ogg')
    # Many pages, and a stream that can't seek, so lavf never reads the last page.
    ffmpeg('-i', d/'a.wav', *vorbis, '-page_duration', 100000, d/'pages.ogg')
    shutil.copy(d/'a.ogg', d/'a_unseekable.ogg')
    # The last granule too low, by more than the last packet's padding.
    ffmpeg('-i', d/'c.wav', *vorbis, d/'granule_src.ogg')
    plain = (d/'granule_src.ogg').read_bytes()
    for name, low in ('granule', 100000), ('granule_near', 20000), ('granule_7000', 7000), ('granule_100', 100):
        b = bytearray(plain)
        at = b.rindex(b'OggS') + 6
        struct.pack_into('<q', b, at, struct.unpack_from('<q', b, at)[0] - low)
        (d/f'{name}.ogg').write_bytes(b)
    # A pre-skip past the bound.
    b = bytearray((d/'a.opus').read_bytes())
    struct.pack_into('<H', b, b.index(b'OpusHead') + 10, 30000)
    (d/'pre_skip.opus').write_bytes(b)
    ffmpeg('-i', d/'a.wav', '-c:a', 'pcm_s16be', d/'a.aiff')
    ffmpeg('-i', d/'b.wav', '-c:a', 'pcm_s16be', d/'b.aiff')


class Player:
    def __init__(self, exe, root, aud, tmp):
        self.e = Emu(exe, root, aud, tmp)
        self.e.run(0.3)
        self.seen = 0

    def play(self, name, next_name=None, seeks=(), during=None):
        """Plays name (and a preloaded next) to the end, returning the left channel's differences, and each seek's index
        in them. seeks: (at seconds of played time, to seconds). during: a command for 0.3 s in."""
        e = self.e
        first = len(e.chunks)
        e.send_cmd('{set-songurl\\/mnt/mmc/%s|ss=0|0|0|1|0|90}' % name)
        e.run(0.05)
        if next_name:
            e.send_cmd('{set-gapless\\/mnt/mmc/%s|ss=0|0|0|1|90}' % next_name)
        if during:
            e.run(0.3)
            e.send_cmd(during)
        for at, to in seeks:
            e.run(at)
            e.send_cmd('{mcl-seek\\%d}' % to)
            e.run(0.02)
        idle = 0
        for _ in range(400):
            n = len(e.chunks)
            e.run(0.25)
            idle = idle + 1 if len(e.chunks) == n else 0
            if idle >= 8 and len(e.chunks) > first: break
        marks = [c for c in e.drops if c >= first]
        e.drops.clear()
        out, at = [], []
        for i, (rate, ch, fmt, data) in enumerate(e.chunks[first:]):
            if first + i in marks: at.append(len(out))
            if fmt in (2, 3):
                a, k = array.array('h', data[:len(data) // 2 * 2]), 1 / 32768
                if fmt == 3: a.byteswap()
            elif fmt == 14: a, k = array.array('f', data[:len(data) // 4 * 4]), 1
            else: a, k = array.array('i', data[:len(data) // 4 * 4]), 1 / 2**31
            out += [x * k for x in a[::ch]]
        del e.chunks[first:]
        return differences(out), at


failed = []
def check(name, got, want):
    ok = got == want
    print(f'  {"ok  " if ok else "FAIL"} {name}: {got}' + ('' if ok else f', wanted {want}'), flush=True)
    if not ok: failed.append(name)


def pair(player, sources, name, a, b, kept, seam, after=0):
    """Plays a into b. kept: A's first sample's place in the output (priming left in). seam: what lies between A's
    last true sample and B's first. after: what B leaves past its last."""
    out, _ = player.play(a, b)
    A, B = sources
    oa, qa = find(out, A, kept - 4000, kept + 8000)
    ob, qb = find(out, B, oa + len(A) - 4000, oa + len(A) + 8000)
    print(f'  {name}: A at {oa} (q {qa:.2f}), B at {ob} (q {qb:.2f}), seam {ob - oa - len(A) - 1}, {len(out) + 1} frames', flush=True)
    check(f'{name} found', qa > 0.5 and qb > 0.5, True)
    # The differences lose each track's first sample: A's and B's offsets are one less than the sample's, both the same.
    check(f'{name} kept', oa, kept)
    check(f'{name} seam', ob - oa - len(A) - 1, seam)
    check(f'{name} after', len(out) - ob - len(B), after)


def seek(player, sources, name, file, next_name, to, want):
    """Seeks from 0.6 s in; want: the source frame the first frames after the seek must be."""
    out, at = player.play(file, next_name, [(0.6, to)])
    A, B = sources
    assert at, f'{name}: no seek happened'
    start, frames = at[-1], len(A) + 1
    # A template from further on in the source, so a decoder's first frame after the flush doesn't matter.
    span = 22050
    source_at = to * frames // SECONDS + span
    t = A[source_at:source_at + TEMPLATE]
    best, where = -1, 0
    for i in range(start, min(start + span + 6000, len(out) - TEMPLATE)):
        c = math.sumprod(out[i:i + TEMPLATE], t)
        if c > best: best, where = c, i
    landed = source_at - (where - start)
    end = start + frames - landed
    ob, qb = find(out, B, end - 4000, end + 4000)
    q = best / (math.sqrt(math.sumprod(t, t) * math.sumprod(*[out[where:where + TEMPLATE]] * 2)) or 1)
    print(f'  {name}: first frame after the seek is source frame {landed} (q {q:.2f}), B at {ob} (q {qb:.2f})', flush=True)
    check(f'{name} found', q > 0.5 and qb > 0.5, True)
    check(f'{name} landed', landed, want)
    check(f'{name} seam', ob - end, 0)


def whole(player, file, want):
    """Frames the file comes to on its own."""
    out, _ = player.play(file)
    check(file, len(out) + 1, want)


def like_stock(player, stock, file, less=0):
    """Comes to what stock's does, less a part the file's numbers do say."""
    out, _ = stock.play(file)
    was = len(out) + 1
    out, _ = player.play(file)
    check(f'{file} (stock {was}, less {less})', len(out) + 1, was - less)


def crossfade(exe, root, aud, tmp, sources):
    """Fading 1 s, the next track starts exactly 1 s before the true end: not 1 s before the padding's."""
    player = Player(exe, root, aud, tmp)
    (tmp/'tmp_q2xfade').write_bytes(struct.pack('<i', 1000))
    out, _ = player.play('c.m4a', 'd.m4a', during='{mcl-seteq\\0|0|0|0|0|0|0|0|0|0|1}')
    (tmp/'tmp_q2xfade').unlink()
    oa, qa = find(out, sources[0], -4000, 8000)
    ob, qb = find(out, sources[1], LONG - 44100 - 4000, LONG - 44100 + 4000)
    print(f'  Crossfade 1 s: C at {oa} (q {qa:.2f}), D at {ob} (q {qb:.2f})', flush=True)
    check('Crossfade found', qa > 0.5 and qb > 0.5, True)
    check('Crossfade: next starts 1 s before the true end', ob - oa, LONG - 44100)


def bluetooth(exe, root, aud, tmp):
    """Through the 48 kHz resampler: M4A's seam against FLAC's, the same music with no seam at all."""
    player = Player(exe, root, aud, tmp)
    for command in '{mcl-setoutputway\\1}', '{mcl-setvol\\100}', '{mcl-softvolflag\\1}':  # without a volume, silence
        player.e.send_cmd(command)
        player.e.run(0.05)
    frames = SAMPLES * 48000 // 44100
    ref, _ = player.play('a.flac', 'b.flac')
    out, _ = player.play('a.m4a', 'b.m4a')
    oa, qa = find(out, ref, -4000, 4000)
    ob, qb = find(out, ref[frames:], frames - 4000, frames + 4000)
    print(f'  Bluetooth: A at {oa} (q {qa:.2f}), B at {ob} (q {qb:.2f}), {len(out) + 1} frames, FLAC {len(ref) + 1}', flush=True)
    check('Bluetooth found', qa > 0.5 and qb > 0.5, True)
    check('Bluetooth seam', ob - oa - frames, 0)


def main():
    build = pathlib.Path(sys.argv[1])
    exe = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else build/'peq/hciplayer'
    work = pathlib.Path(tempfile.mkdtemp(prefix='seam-', dir=build))
    try:
        subprocess.check_call(['unsquashfs', '-q', '-n', '-d', work/'root', build/'stock.squashfs', 'lib', 'usr/lib', 'usr/bin/hciplayer'], stdout=subprocess.DEVNULL)
        aud, tmp = work/'aud', work/'tmp'
        aud.mkdir(), tmp.mkdir()
        generate(aud)
        sources = left(aud/'a.wav'), left(aud/'b.wav')
        sources48 = left(aud/'a48.wav'), left(aud/'b48.wav')
        player = Player(exe, work/'root', aud, tmp)
        stock = Player(work/'root/usr/bin/hciplayer', work/'root', aud, tmp)

        print('exact already')
        pair(player, sources, 'AIFF', 'a.aiff', 'b.aiff', 0, 0)
        pair(player, sources, 'FLAC', 'a.flac', 'b.flac', 0, 0)
        pair(player, sources, 'ALAC', 'a.alac.m4a', 'b.alac.m4a', 0, 0)
        pair(player, sources, 'MP3', 'a.mp3', 'b.mp3', 0, 0)
        print('M4A')
        pair(player, sources, 'edit list', 'a.m4a', 'b.m4a', 0, 0)
        pair(player, sources, 'edit list, whole frames', 'a_elst.m4a', 'b_elst.m4a', 0, 0)
        pair(player, sources, 'iTunSMPB', 'a_tag.m4a', 'b_tag.m4a', 0, 0)
        pair(player, sources, 'iTunSMPB, remainder only', 'a_tag_remainder.m4a', 'b_tag_remainder.m4a', 0, 0)
        pair(player, sources, 'iTunSMPB, priming over a frame', 'a_tag_2112.m4a', 'b_tag.m4a', -1088, 0)
        print('Ogg')
        pair(player, sources48, 'Opus', 'a.opus', 'b.opus', 0, 0)
        pair(player, sources, 'Vorbis', 'a.ogg', 'b.ogg', 0, 0)
        print('Preloads stay separate')
        pair(player, sources, 'M4A into FLAC', 'a.m4a', 'b.flac', 0, 0)
        pair(player, sources, 'FLAC into M4A', 'a.flac', 'b.m4a', 0, 0)
        pair(player, sources, 'trimmed into junk', 'a_tag.m4a', 'b_junk_total.m4a', 0, 0, PADDING)
        pair(player, sources, 'junk into trimmed', 'a_junk_total.m4a', 'b_tag.m4a', 0, PADDING)
        print('Numbers that are nonsense')
        pair(player, sources, 'total past the end', 'a_junk_total.m4a', 'b_junk_total.m4a', 0, PADDING, PADDING)
        pair(player, sources, 'tag from before an edit', 'a_junk_edit.m4a', 'b_junk_edit.m4a', 0, PADDING, PADDING)
        pair(player, sources, 'priming lavf refuses', 'a_junk_priming.m4a', 'b_junk_priming.m4a', 1024, 1024 + PADDING, PADDING)
        print('An end that is wrong by more than the last packet')
        for file in 'stts_136.m4a', 'stts_0.m4a', 'last_delta_0.m4a', 'priming0.m4a', 'priming0_small.m4a', 'fragmented_unseekable.m4a', \
                'timebase_high.m4a', 'media_time_big.m4a', 'granule_near.ogg', 'granule_7000.ogg', 'granule_100.ogg', \
                'chain_rates.ogg', 'pre_skip.opus', 'stale.m4a', 'stale_near.m4a', 'stale_300.m4a', 'stale_frame.m4a':
            like_stock(player, stock, file)
        # A length that understates by more than stock's second past it: stock's forced EOF cut there, and only an MP3's frame
        # count does now (docs/internals.md#forced-eof), so every packet comes out.
        whole(player, 'timebase.m4a', (LONG + 1024 + 1023) // 1024 * 1024)
        whole(player, 'timebase_edit.m4a', (SAMPLES + 1024 + 1023) // 1024 * 1024)
        whole(player, 'granule.ogg', len(stock.play('granule_src.ogg')[0]) + 1)
        # ponytail: wrong by less than what's left of the last packet, it can't be told from padding, and that much goes.
        like_stock(player, stock, 'stale_100.m4a', (SAMPLES * 2 + 1024 + 1023) // 1024 * 1024 - 1024 - LONG + 100)
        like_stock(player, stock, 'two_edits.m4a', 1024)  # the priming, as the first edit's media time says
        for drop in range(4):
            like_stock(player, stock, f'two_aus_{drop}.m4a')  # half read, then past it: nothing goes
        like_stock(player, stock, 'chain.ogg', 120)  # the last link's padding
        like_stock(player, stock, 'chain_serials.ogg', 120)
        whole(player, 'frag.m4a', LONG + 1024)  # its priming kept, its padding cut, no music lost
        print('Chained Opus: each link comes to its true length')
        for frames, total in CHAIN_LINKS.items():
            whole(player, f'chain_{frames}.opus', total)
        whole(player, 'chain_three.opus', 240480)
        whole(player, 'chain.opus', 192000)
        print('Short tracks, cut tracks, many pages, no last page')
        for frames in SHORT:
            like_stock(player, stock, f's{frames}.ogg')
        like_stock(player, stock, 'cut.ogg')
        like_stock(player, stock, 'a_unseekable.ogg')
        whole(player, 's8000.m4a', 8000)
        whole(player, 's8000.opus', 8000)
        pair(player, sources, 'Vorbis of many pages', 'pages.ogg', 'b.ogg', 0, 0)
        print('HE-AAC keeps what stock plays')
        for he in 'he', 'ps':
            for shape in 'core', 'output', 'lying':
                like_stock(player, stock, f'{he}_{shape}.m4a')
        print('An edit in another timescale: up to a tick kept, none lost')
        pair(player, sources, 'priming past a block', 'media_time_8192.m4a', 'b.m4a', -7168, 0)
        pair(player, sources, 'priming past two blocks', 'media_time_16000.m4a', 'b.m4a', -14976, 0)
        pair(player, sources, 'tag and edit list', 'tag_and_edit.m4a', 'b.m4a', 0, 0)
        pair(player, sources, 'tag and edit list over a frame', 'tag_and_edit_2112.m4a', 'b.m4a', -1088, 0)
        pair(player, sources, 'tag far short', 'tag_far_short.m4a', 'b.m4a', 0, 0)
        pair(player, sources, 'edit far short', 'edit_short.m4a', 'b.m4a', 0, 0)
        pair(player, sources, 'edit past the end', 'edit_long.m4a', 'b.m4a', 0, 0)
        whole(player, 'edge_1000.m4a', (2000 + 1) * 441 // 10 + 1)
        whole(player, 'edge_600.m4a', (1200 + 1) * 735 // 10 + 1)
        print('Nothing to go on')
        pair(player, sources, 'ADTS', 'a.aac', 'b.aac', 1024, 1024 + PADDING, PADDING)
        print('Seeks')
        seek(player, sources, 'edit list, to 0', 'a.m4a', 'b.m4a', 0, 0)
        seek(player, sources, 'iTunSMPB, to 0', 'a_tag.m4a', 'b_tag.m4a', 0, 0)
        seek(player, sources48, 'Opus, to 0', 'a.opus', 'b.opus', 0, 0)
        seek(player, sources, 'Vorbis, to 0', 'a.ogg', 'b.ogg', 0, 0)
        # Stock's landing, where a packet starts: a frame later would be priming cut again.
        seek(player, sources, 'edit list, to 1', 'a.m4a', 'b.m4a', 1, 44032)
        seek(player, sources, 'iTunSMPB, to 1', 'a_tag.m4a', 'b_tag.m4a', 1, 44032)
        seek(player, sources48, 'Opus, to 1', 'a.opus', 'b.opus', 1, 47688)
        seek(player, sources, 'Vorbis, to 1', 'a.ogg', 'b.ogg', 1, 45760)
        del player, stock
        print('Crossfade and Bluetooth')
        crossfade(exe, work/'root', aud, tmp, (left(aud/'c.wav'), left(aud/'d.wav')))
        bluetooth(exe, work/'root', aud, tmp)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if failed:
        print(f'player_seam: {len(failed)} FAILED: {failed}')
        sys.exit(1)
    print('player_seam: all as expected')


if __name__ == '__main__':
    main()
