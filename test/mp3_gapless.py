#!/usr/bin/env python3
"""Gapless parser and streaming PCM: synthetic boundaries and real LAME/FFmpeg files.
Requires host cc, ffmpeg, lame and libavcodec/libavutil/libmad development files.
No audio hardware or downloaded fixtures.
"""
import ctypes as C
from pathlib import Path
import random
import struct
import subprocess
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.check_output(args, stderr=subprocess.PIPE)


with tempfile.TemporaryDirectory() as directory:
    tmp = Path(directory)
    src = tmp/'check.c'
    src.write_text('#define PEQ_HOST\n#include "' + str(ROOT/'patch/mp3_gapless.c') + '"\n' + r'''
void *create(const unsigned char *h, unsigned n) {
    mp3_private *p = calloc(1, sizeof(*p));
    if (!mp3_gapless(h, n, &p->start, &p->padding, &p->rate, &p->channels)) { free(p); return 0; }
    p->skip = p->start;
    return p;
}
int process(mp3_private *p, unsigned char *a, unsigned n, unsigned bps) {
    af_data d = {a, n, p->rate, p->channels, 0, bps};
    mp3_trim(p, &d);
    memmove(a, d.audio, d.len);
    return d.len;
}
void destroy(mp3_private *p) { free(p->tail); free(p); }
''')
    run('cc', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC', '-fsanitize=undefined',
        '-fno-sanitize-recover=undefined', str(src), '-o', str(tmp/'check.so'))
    lib = C.CDLL(str(tmp/'check.so'))
    lib.create.argtypes, lib.create.restype = [C.c_char_p, C.c_uint], C.c_void_p
    lib.process.argtypes = [C.c_void_p, C.c_void_p, C.c_uint, C.c_uint]
    lib.destroy.argtypes = [C.c_void_p]
    lib.mp3_gapless.argtypes = [C.c_char_p, C.c_uint] + [C.POINTER(C.c_uint)] * 4
    decoder = tmp/'decode.c'
    decoder.write_text(r'''
#include <assert.h>
#include <stdio.h>
#include <libavcodec/avcodec.h>
#include <libavutil/samplefmt.h>
int main(void) {
    const AVCodec *codec = avcodec_find_decoder_by_name("mp3float");
    AVCodecContext *ctx = avcodec_alloc_context3(codec);
    AVPacket *pkt = av_packet_alloc();
    AVFrame *f = av_frame_alloc();
    unsigned n;
    assert(codec && ctx && pkt && f && avcodec_open2(ctx, codec, NULL) == 0);
    while (fread(&n, sizeof n, 1, stdin) == 1) {
        assert(av_new_packet(pkt, n) == 0 && fread(pkt->data, 1, n, stdin) == n);
        assert(avcodec_send_packet(ctx, pkt) == 0);
        while (avcodec_receive_frame(ctx, f) == 0) {
            int channels = f->ch_layout.nb_channels;
            assert(f->format == AV_SAMPLE_FMT_FLTP || f->format == AV_SAMPLE_FMT_FLT);
            for (int i = 0; i < f->nb_samples; ++i)
                for (int c = 0; c < channels; ++c) {
                    float v = f->format == AV_SAMPLE_FMT_FLTP ? ((float *)f->data[c])[i]
                              : ((float *)f->data[0])[i * channels + c];
                    assert(fwrite(&v, sizeof v, 1, stdout) == 1);
                }
        }
        av_packet_unref(pkt);
    }
    av_frame_free(&f);
    av_packet_free(&pkt);
    avcodec_free_context(&ctx);
    return 0;
}
''')
    run('cc', '-Wall', '-Wextra', '-Werror', str(decoder), '-o', str(tmp/'decode'),
        *run('pkg-config', '--cflags', '--libs', 'libavcodec', 'libavutil').decode().split())

    def raw_decode(raw):
        packets, at = [], 0
        rates = (44100, 48000, 32000)
        bitrates = ((0,32,40,48,56,64,80,96,112,128,160,192,224,256,320),
                    (0,8,16,24,32,40,48,56,64,80,96,112,128,144,160))
        while at + 4 <= len(raw) and raw[at] == 255:
            h = raw[at:at+4]
            version = h[1] >> 3 & 3
            rate = rates[h[2] >> 2 & 3] >> (0 if version == 3 else 1 if version == 2 else 2)
            size = (144000 if version == 3 else 72000) * bitrates[version != 3][h[2] >> 4] // rate + (h[2] >> 1 & 1)
            assert size and at + size <= len(raw)
            packets.append(struct.pack('<I', size) + raw[at:at+size])
            at += size
        return subprocess.check_output([str(tmp/'decode')], input=b''.join(packets))

    mad = tmp/'mad.c'
    mad.write_text(r'''
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <mad.h>
int main(int argc, char **argv) {
    assert(argc == 2);
    FILE *f = fopen(argv[1], "rb");
    assert(f && !fseek(f, 0, SEEK_END));
    long n = ftell(f);
    unsigned char *data = calloc(1, n + MAD_BUFFER_GUARD);
    rewind(f);
    assert(data && fread(data, 1, n, f) == (unsigned long)n);
    fclose(f);
    struct mad_stream stream;
    struct mad_frame frame;
    struct mad_synth synth;
    mad_stream_init(&stream); mad_frame_init(&frame); mad_synth_init(&synth);
    mad_stream_buffer(&stream, data, n + MAD_BUFFER_GUARD);
    while (1) {
        if (mad_frame_decode(&frame, &stream)) {
            if (stream.error == MAD_ERROR_BUFLEN) break;
            assert(MAD_RECOVERABLE(stream.error));
            continue;
        }
        mad_synth_frame(&synth, &frame);
        for (unsigned i = 0; i < synth.pcm.length; ++i)
            for (unsigned c = 0; c < synth.pcm.channels; ++c) {
                float v = (float)synth.pcm.samples[c][i] / MAD_F_ONE;
                assert(fwrite(&v, sizeof v, 1, stdout) == 1);
            }
    }
    mad_frame_finish(&frame); mad_stream_finish(&stream); free(data);
    return 0;
}
''')
    run('cc', '-Wall', '-Wextra', '-Werror', str(mad), '-o', str(tmp/'mad'),
        *run('pkg-config', '--cflags', '--libs', 'mad').decode().split())

    def parse(h):
        fields = [C.c_uint() for _ in range(4)]
        return tuple(x.value for x in fields) if lib.mp3_gapless(h, len(h), *map(C.byref, fields)) else None

    def header(version=3, mono=False, flags=15, crc=False, shifted=False, encoder=b'LAME3.100', delay=576, pad=1200):
        # 128 kbit/s MPEG1 or 64 kbit/s MPEG2/2.5, sampling frequency index 0.
        rate = 44100 >> (0 if version == 3 else 1 if version == 2 else 2)
        size = (144000 * 128 if version == 3 else 72000 * 64) // rate
        h = bytearray(size)
        h[:4] = bytes([255, 224 | version << 3 | 2 | (not crc), 0x90 if version == 3 else 0x80, 192 if mono else 0])
        at = 4 + ((17 if mono else 32) if version == 3 else (9 if mono else 17)) + 2 * shifted
        h[at:at+8] = b'Xing' + struct.pack('>I', flags)
        at += 8
        if flags & 1:
            h[at:at+4] = struct.pack('>I', 100)
            at += 4
        at += (4 if flags & 2 else 0) + (100 if flags & 4 else 0) + (4 if flags & 8 else 0)
        h[at:at+len(encoder)] = encoder
        h[at+21:at+24] = ((delay << 12) | pad).to_bytes(3, 'big')
        crc = 0
        for byte in h[:at+34]:
            crc ^= byte
            for _ in range(8):
                crc = crc >> 1 ^ 0xa001 if crc & 1 else crc >> 1
        h[at+34:at+36] = crc.to_bytes(2, 'big')
        return bytes(h)

    for version in (3, 2, 0):
        for mono in (False, True):
            for flags in range(1, 16, 2):
                for crc, shifted in ((False, False), (True, False), (True, True)):
                    h = header(version, mono, flags, crc, shifted)
                    expected = ((1152 if version == 3 else 576) + 1105, 671,
                                44100 >> (0 if version == 3 else 1 if version == 2 else 2), 1 if mono else 2)
                    assert parse(h) == expected, (version, mono, flags, crc, shifted, parse(h))
                    for n in range(len(h)):
                        assert parse(h[:n]) is None
    # LAME's and FFmpeg's names in any case, a nonzero revision nibble, and any other printable name whose CRC holds.
    for encoder in (b'Lavc61.1', b'Lavf61.1', b'lavf', b'L3.99r', b'L3.100', b'GOGO3.13', b'FhGIIS 4'):
        assert parse(header(encoder=encoder)) == parse(header()), encoder
    for encoder, kept in ((b'L3.99r', False), (b'GOGO3.13', False), (b'LAME3.100', True), (b'Lame3.100', True), (b'Lavf61.1', True)):
        h = bytearray(header(encoder=encoder))
        h[190] ^= 1  # the CRC, with all four optional fields in the frame
        assert parse(bytes(h)) == (parse(header()) if kept else (1152, 0, 44100, 2)), encoder
    assert parse(header(encoder=b'LAME3.100\x14')) == parse(header())
    # A real encoder's own spelling, valid CRC and all. FFmpeg's check is case-sensitive too and mpg123's isn't there.
    assert parse(header(encoder=b'Lame3.100')) == parse(header())
    # A tag frame without a usable extension (nothing, binary, a longer name running over the field bytes, or a
    # count missing): no delays to trim, but the frame's own silence goes.
    frame_only = (1152, 0, 44100, 2)
    for h in (header(flags=0), header(flags=14), header(encoder=b''), header(encoder=bytes(range(200, 230))),
              header(encoder=b'Some Encoder Version 1.2.3 build 45'), header(delay=0, pad=0),
              header(delay=4095), header(pad=4095)):
        assert parse(h) == frame_only, h[36:60]
    assert parse(header(version=2, flags=0)) == (576, 0, 22050, 2)
    assert parse(header(mono=True, flags=0)) == (1152, 0, 44100, 1)
    # A protected frame's CRC is not side info; the side info is what says a frame has no audio.
    for crc_layout in (False, True):
        h = bytearray(header(flags=0, crc=True, shifted=crc_layout))
        h[4:6] = b'\x12\x34'
        assert parse(bytes(h)) == frame_only
        h[6] = 1
        assert parse(bytes(h)) is None
    for at in (4, 5, 20, 35):
        h = bytearray(header(flags=0))
        h[at] = 1
        assert parse(bytes(h)) is None, at
    vbri = bytearray(header(flags=0))
    vbri[36:48] = b'VBRI\0\1' + bytes(6)
    assert parse(bytes(vbri)) == frame_only
    assert parse(bytes(vbri[:417])) == frame_only
    # VBRI's version, and a VBRI frame carrying audio or too short for its name, stay stock's.
    for at, value in ((41, 2), (40, 1), (10, 1)):
        h = bytearray(vbri)
        h[at] = value
        assert parse(bytes(h)) is None, at
    # Audio frames and non-frames stay stock's.
    assert parse(bytes(417)) is None and parse(b'') is None and parse(bytes(500)) is None
    audio = bytearray(header(flags=0))
    audio[36:40] = bytes(4)
    assert parse(bytes(audio)) is None
    # iTunSMPB, with the counts of the real iTunes 12.7.0.166 file measured in docs/internals.md: no tag frame at all there.
    lib.mp3_smpb.argtypes = [C.c_char_p, C.c_uint, C.c_char_p] + [C.c_void_p] * 4
    def smpb(h, text):
        fields = [C.c_uint() for _ in range(4)]
        return tuple(x.value for x in fields) if lib.mp3_smpb(h, len(h), text, *map(C.byref, fields)) else None
    def itunes(priming=0x210, remainder=0x86a, samples=0x66486):
        return b' 00000000 %08X %08X %016X 00000000 0002DA9D 00000000' % (priming, remainder, samples)
    audio = bytes.fromhex('fffba040') + bytes(16) + b'\x01' + bytes(501)  # 160 kbit/s, no tag name
    assert parse(audio) is None
    assert smpb(audio, itunes()) == (1057, 1625, 44100, 2)
    assert smpb(audio, itunes().lower()) == (1057, 1625, 44100, 2)
    assert smpb(audio, itunes(priming=0)) == (529, 1625, 44100, 2)
    assert smpb(audio, itunes(priming=16384))[0] == 16384 + 529
    assert smpb(audio, itunes(remainder=4095))[1] == 4095 - 529
    assert smpb(audio, itunes(remainder=100))[1] == 0
    for text in (itunes(priming=16385), itunes(remainder=4096), itunes(samples=0), itunes(samples=1 << 40), b'', b' 0 210 86A',
                 b' 0 210 86A 66486'.replace(b'210', b'00000000000000210'), b'iTunSMPB'):
        assert smpb(audio, text) is None, text
    assert smpb(audio[:300], itunes()) is None and smpb(bytes(522), itunes()) is None
    # A tag frame: the sum has to come to its frames, which leave it out, and its own samples go too.
    def counted(frames, version=3, name=b'Info'):
        h = bytearray(header(version=version, flags=1, encoder=b''))
        h[36 if version == 3 else 21:48 if version == 3 else 33] = name + struct.pack('>II', 1, frames)
        return bytes(h)
    assert smpb(counted(366), itunes()) == (1152 + 1057, 1625, 44100, 2)
    assert smpb(counted(366, name=b'Xing'), itunes()) == (1152 + 1057, 1625, 44100, 2)
    assert smpb(counted(732, version=2), itunes()) == (576 + 1057, 1625, 22050, 2)
    assert smpb(counted(367), itunes()) is None and smpb(counted(365), itunes()) is None
    assert smpb(counted(366), itunes(remainder=0x86b)) is None
    assert smpb(counted(0), itunes()) == (1152 + 1057, 1625, 44100, 2)  # no count, nothing to check, as FFmpeg
    vbri_count = bytearray(vbri)
    vbri_count[50:54] = struct.pack('>I', 366)
    assert smpb(bytes(vbri_count), itunes()) == (1152 + 1057, 1625, 44100, 2)
    vbri_count[50:54] = struct.pack('>I', 365)
    assert smpb(bytes(vbri_count), itunes()) is None
    has_audio = bytearray(counted(366))
    has_audio[10] = 1
    assert smpb(bytes(has_audio), itunes()) is None
    # Tail tags: what stays of the file is what the demuxer plays, an APEv2 tag's only if its footer is sane, and
    # says it has a header for the caller to find.
    lib.mp3_tail.argtypes, lib.mp3_tail.restype = [C.c_char_p, C.c_uint, C.c_void_p], C.c_longlong
    def tail(t):
        ape = C.c_longlong()
        return lib.mp3_tail(t, len(t), C.byref(ape)), ape.value
    def ape(size, flags=0x80000000, version=2000):
        return b'APETAGEX' + struct.pack('<IIII', version, size, 1, flags) + bytes(8)
    id3v1 = b'TAG' + bytes(125)
    for t, want in ((bytes(160), (0, 0)), (bytes(32) + id3v1, (128, 0)), (bytes(128) + ape(5000), (0, 5032)),
                    (ape(5000) + id3v1, (128, 5160)), (bytes(40), (0, 0)), (b'TAG', (0, 0)),
                    (bytes(128) + ape(5000, flags=0), (0, 0)),               # no header to confirm it
                    (ape(5000, flags=0) + id3v1, (128, 0)),
                    (bytes(128) + ape(5000, flags=0xa0000000), (0, 0)),      # this is a header, not a footer
                    (bytes(128) + ape(5000, version=2001), (0, 0)), (bytes(128) + ape(31), (0, 0))):
        assert tail(t) == want, (t[-32:], want)
    assert parse(header(pad=100))[1] == 0
    h = bytearray(header())
    h[2] = 0  # free-format: no trustworthy first-frame boundary
    assert parse(bytes(h)) is None

    def trim(h, pcm, bps, chunks):
        p = lib.create(h, len(h))
        assert p
        unit = parse(h)[3] * bps
        output, at, i = [], 0, 0
        while at < len(pcm):
            n = min(chunks[i % len(chunks)] * unit, len(pcm) - at)
            a = C.create_string_buffer(pcm[at:at+n])
            used = lib.process(p, a, n, bps)
            assert 0 <= used <= n and used % unit == 0
            output.append(a.raw[:used])
            at += n
            i += 1
        lib.destroy(p)
        return b''.join(output)

    rng = random.Random(529)
    for bps in (1, 2, 3, 4):
        for mono in (False, True):
            for pad in (100, 529, 1200):
                h = header(mono=mono, pad=pad)
                start, tail, _, channels = parse(h)
                unit = channels * bps
                pcm = rng.randbytes(20000 * unit)
                for chunks in ([1], [1, 17, 100, 3000, 7], [25000]):
                    assert trim(h, pcm, bps, chunks) == pcm[start*unit:len(pcm)-tail*unit]

    cases = 0
    for rate, channels in ((44100, 1), (44100, 2), (22050, 2), (11025, 1)):
        wav = tmp/'input.wav'
        with wave.open(str(wav), 'wb') as f:
            f.setparams((channels, 2, rate, 0, 'NONE', 'not compressed'))
            f.writeframes(b''.join(struct.pack('<h', rng.randrange(-12000, 12000))
                                   for _ in range((rate + 317) * channels)))
        for encoder, options in (('lame', ['-V', '2']), ('lame', ['-b', '128', '-p']),
                                 ('ffmpeg', ['-q:a', '2']), ('ffmpeg', ['-b:a', '128k'])):
            mp3 = tmp/'encoded.mp3'
            if encoder == 'lame':
                run('lame', '--silent', *options, str(wav), str(mp3))
            else:
                run('ffmpeg', '-v', 'error', '-y', '-i', str(wav), '-c:a', 'libmp3lame', *options, str(mp3))
            raw = mp3.read_bytes()
            at = 10 + sum((raw[6+i] & 127) << (21-7*i) for i in range(4)) if raw[:3] == b'ID3' else 0
            h = raw[at:at+2048]
            assert parse(h), (encoder, options, rate, channels)
            def decode(path):
                return run('ffmpeg', '-v', 'error', '-i', str(path), '-f', 'f32le', '-acodec', 'pcm_f32le', '-')
            expected = decode(mp3)
            pcm = raw_decode(raw[at:])
            actual = trim(h, pcm, 4, [1, 31, 2048, 576, 17])
            assert actual == expected, (encoder, options, rate, channels, len(actual), len(expected))
            assert len(actual) == (rate + 317) * channels * 4
            bare = tmp/'bare.mp3'
            bare.write_bytes(raw[at:])
            mad_pcm = run(str(tmp/'mad'), str(bare))
            mad_actual = trim(h, mad_pcm, 4, [31, 1152, 17, 2048])
            assert len(mad_actual) == len(expected), ('libmad', encoder, options, rate, len(mad_actual), len(expected))
            error = sum((a[0] - b[0])**2 for a, b in zip(struct.iter_unpack('<f', mad_actual),
                                                       struct.iter_unpack('<f', expected))) / (len(expected)//4)
            assert error < 1e-8, ('libmad sample alignment', encoder, options, rate, error)
            cases += 1
    print(f'MP3 gapless: MPEG1/2/2.5, optional fields, CRC, malformed/truncated tags, PCM boundaries; {cases} real LAME/FFmpeg files match FFmpeg sample for sample, libmad lengths/alignment agree: OK')
