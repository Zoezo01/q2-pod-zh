"""Checked Q2 V1.32 PEQ hooks and target ABI imports; no vendor code is distributed."""
import re
import struct
from build import ROOT, FLAGS, GP, FUNCTIONS, GLOBALS, PRIVATE_FUNCTIONS, append_payload, check, fileoff, run, sha, symbols, jal, j
from ipod import patch_word

PLAYER_SHA = '9c3f8c6d01f1ba62392622f6098b06a36b3e4f022a5468eaca6a5803e74f8e11'
PLAYER_BASE = 0xe10000  # stock final LOAD ends at 0xe03b58
# demux_audio_open's branch to the whole-file frame walk for Xing/VBRI MP3s; see docs/internals.md.
VBR_SCAN = 0x48fb3c
# MPlayer's pause loop sleeps usec_sleep(20000) between command checks: 50 wakes a second for as long as the
# track stays paused. Its sleep becomes a futex wait that the command thread's handler call wakes
# (hci_pause_wait, hci_command in peq_player.c, docs/internals.md#battery).
PAUSE_WAIT = (0x430230, 0x0c12931c)  # jal usec_sleep
CMD_DONE = (0x42d7c4, 0x0c10af88)  # jal 0x42be20, the command handler
# Stopped, the idle loop's usleep(10000) becomes a futex wait on the play flag, which the command handler's
# wrapper wakes (CMD_DONE; peq_player.c, docs/internals.md#battery).
IDLE_WAIT = (0x42d9c8, 0x0c2b6bf4)  # jal usleep
# The audio demuxer's seek slot (demuxer_desc_audio at 0x89fd90) holds demux_audio_seek; see docs/internals.md.
SEEK_SLOT, STOCK_SEEK = 0x89fdbc, 0x48d1ac
# demux_audio_open's calls to the ID3v2 frame walker (0x48e6b0) that feeds ReplayGain; see docs/internals.md.
ID3_CALLS, STOCK_ID3 = (0x48f704, 0x4923d8), 0x48e6b0
# lavf_check_file's calloc of lavf_priv_t (0xc38): room for patch/lavf_gapless.c's window, freed with it.
LAVF_PRIV = (0x4aea4c, 0x24040c38, 0x24040c58)
# mov_read_trak's av_freep(&sc->elst_data): its window reads the edit's duration. mov_read_close frees it instead.
MOV_EDITS = (0x5168f8, jal(0x6f4118), 0)
# Audio private allocation, pre-filter PCM call, and audio demuxer close slot.
MP3_ALLOC, MP3_FILTER, MP3_CLOSE = 0x48fb90, 0x47f8e0, 0x89fdb8
# ad_libmad read_frame's demux_read_data call (0x484c0c), which gets the last frame its guard bytes.
MP3_READ, DEMUX_READ = 0x4b248c, 0x484c0c
# The audio demuxer's fill slot.
MP3_FILL, DEMUX_FILL = 0x89fdb0, 0x48c184
# The equalizer filter's descriptor (pinned) and its open slot, which becomes peq_open.
EQ_DESC, EQ_OPEN_SLOT = 0x893df8, 0x893e0c
# lavcresample's descriptor open slot (af_info at 0x8a4540; stock open 0x4ac2c0), which becomes rs_open: the
# resampler Bluetooth's 48 kHz output appends keeps its state across a gapless change (docs/internals.md).
RS_OPEN_SLOT, RS_OPEN = 0x8a4554, 0x4ac2c0
# ao_alsa's descriptor init slot (0x88f758; stock init 0x438988), which becomes ao_init: on Bluetooth or a USB DAC
# the same request asks for the rate and format it negotiated last time, so a gapless change keeps the PCM open.
AO_INIT_SLOT, AO_INIT = 0x88f760, 0x438988
# Its uninit slot (stock 0x43868c), which becomes ao_uninit: it notes a file played to its end, so ao_init drains
# that track's last audio before a reopen, and a skip stays mute-first (peq_player.c).
AO_UNINIT_SLOT, AO_UNINIT = 0x88f764, 0x43868c
# Stock skips the equalizer above 48 kHz (address, stock word, patched word); see docs/internals.md.
EQ_RATE_GATES = ((0x42cec0, 0x8c429e30, 0),            # mpctx: the three loads below fed only that gate, and faulted with
                 (0x42cec4, 0x8c42002c, 0),            # no sh_audio (docs/internals.md#music-info-between-songs)
                 (0x42cec8, 0x8c42003c, 0),            # sh_audio's rate
                 (0x42ced0, 0x10400098, 0),            # beqz $v0 to "setequalizer off" -> nop
                 (0x42fd84, 0x1440003b, 0x1000003b))   # bnez $v0 to the insert -> b
# The MQA side chain (docs/internals.md#mqa-detection): fill_audio_out_buffers runs every probed stream
# through the MQA decoder while isMQA (0xb10c54) > 0 but plays its output only when isMQA == 2, so the gate
# becomes isMQA == 2 (address, stock word, patched word).
MQA_GATE = ((0x42f8b0, 0x34620040, 0x3c0200b1),   # ori $v0, $v1, 0x40    -> lui $v0, 0xb1
            (0x42f8b4, 0x005d1021, 0x8c420c54),   # addu $v0, $v0, $sp    -> lw $v0, 0xc54($v0)
            (0x42f8b8, 0x8c420000, 0x2442fffe),   # lw $v0, 0($v0)        -> addiu $v0, $v0, -2
            (0x42f8bc, 0x8c420c54, 0),            # lw $v0, 0xc54($v0)    -> nop
            (0x42f8c0, 0x1840000a, 0x1440000a))   # blez $v0, 0x42f8ec    -> bnez $v0, 0x42f8ec
# So MQA is found by the start-of-track probe alone. The detector gives up after 96000 frames, so the probe
# reads 0x300000 >> (codec ^ 15) bytes instead of 0xc00 (0x20000 on output way 2): FLAC (13, decoded to 8-byte
# frames at 24 bit) 0xc0000, past 96000 frames for every eligible format; PCM (11) 0x30000, ad_pcm's a_buffer
# (0x30800) holding no more. $t3 is the codec type here and $v1 is dead.
MQA_PROBE = ((0x42eaf4, 0x3c020002, 0x3c020030),  # lui $v0, 0x2           -> lui $v0, 0x30
             (0x42eaf8, 0x38630002, 0x3963000f),  # xori $v1, $v1, 2       -> xori $v1, $t3, 15
             (0x42eafc, 0x00e3100b, 0x00621006))  # movn $v0, $a3, $v1     -> srlv $v0, $v0, $v1
# ao_alsa's init (0x438988) asks for a 500 ms buffer (0x438a3c) but cuts it to 100 ms on output way 2, a USB
# DAC: two branch delay slots store 100000 us and become nops, so USB keeps 500 ms.
# ponytail: where hciplayer scales a USB DAC's volume itself, a change now lags up to 500 ms, not 100 ms.
USB_BUFFER = ((0x43977c, 0xafc2017c, 0),           # sw $v0, 0x17c($fp)  -> nop  (hw:2,0)
              (0x439a60, 0xafc3017c, 0))           # sw $v1, 0x17c($fp)  -> nop  (hw:1,0)
# The wired outputs' unmute calls release the DAC, wait and only then let audio flow (peq_player.c,
# docs/internals.md#wired-start): (address, stock word, hook).
DAC_RELEASE = ((0x4391b0, 0x0c2b6eec, 'dac_ioctl'),   # end of init: jal ioctl
               (0x42b164, 0x0c10eca0, 'dac_unmute'),  # seek: jal 0x43b280, the unmute helper
               (0x43881c, 0x0c2b6b3c, 'dac_resume'),  # resume: jal snd_pcm_pause
               (0x43afa4, 0x0c2b6c88, 'ao_write'))    # play: jal snd_pcm_writei; the first write after a kept PCM; an unpause writes these frames again
# The DSF, DFF and ISO checks' failure exits store the global stop: on the preload thread they don't (peq_player.c,
# docs/internals.md#a-bad-file-next): (address, stock word, hook). The delay slot's lui stays, unused.
PROBE_STOP = ((0x47b97c, 0xac640e74, 'probe_stop_dsf'),  # sw $a0, 0xe74($v1)
              (0x47a904, 0xac640e74, 'probe_stop_dff'),
              (0x478c10, 0xac640e74, 'probe_stop_iso'))
# The rest that can run on the thread: a base from a hook in place of the first lui, the second lui gone. The fill's own.
STOP_SITES = ((0x478b40, 0x3c0200b1, 'stop_iso'), (0x480184, 0x3c0200b1, 'stop_aac'), (0x4826a4, 0x3c0200b1, 'stop_asf'))
STOP_NOPS = ((0x478b50, 0x3c0200b1, 0), (0x480190, 0x3c0200b1, 0), (0x4826b0, 0x3c0200b1, 0))
STOP_FILL = (0x46c184, 0xac430e74, 'stop_fill')  # sw $v1, 0xe74($v0)
# The header's length ends a track only when it's an MP3's Xing or Info frame count: stock's EOF once the position passed
# it by 1 s, on a file (docs/internals.md#forced-eof). Every other length was an estimate, and cut real audio.
FORCED_EOF = (0x4232bc, 0xac850014, 'forced_eof')  # sw $a1, 0x14($a0)
# A second exit within a UI pass of the last is lost to demo; it waits (peq_player.c, docs/internals.md#a-bad-file-next).
EXIT_HOLD = (0x4226d4, 0x3c040089, 'exit_hold')  # lui $a0, 0x89
# A preload the song outran doesn't publish its stale context (peq_player.c, docs/internals.md#a-preload-too-late):
# the thread's store of 2, the three failure stores of -1, moved to the failure exit, and the cold path's clear.
PRELOAD_PUBLISH = (0x42bb18, 0xac440de8, 'preload_publish')  # sw $a0, 0xde8($v0)
PRELOAD_FAILED = (0x42bba4, 0x00001025, 'preload_failed')    # move $v0, $zero
PRELOAD_FAIL_STORES = ((0x42bb80, 0xac430de8, 0), (0x42bbec, 0xac430de8, 0), (0x42bc14, 0xac430de8, 0))  # sw $v1, 0xde8($v0)
COLD_CLEAR = (0x421a3c, 0xac600de8, 'cold_clear')            # sw $zero, 0xde8($v1)
# mcl-closegapless and the direct stop while the thread runs: 1 becomes 3, so what it was opening isn't played.
PRELOAD_CLOSE = ((0x42c264, 0x24030002, 'preload_close'), (0x42c994, 0x24030002, 'preload_stop'))  # li $v1, 2
# A FLAC seek keeps the file's end (peq_player.c, docs/internals.md#a-flac-seek-near-the-end): the decoder's seek
# gate's failure exit, and the seek's sync scan finding none in what's left of the file.
FLAC_GATE = (0x5b7dc4, 0x1000fd74, 'flac_gate')       # b 0x5b7398
SEEK_NOSYNC = (0x48d5ec, 0x8fa20030, 'seek_nosync')   # lw $v0, 0x30($sp)
SEEK_WORDS = ((0x48d5f0, 0x00822023, 0),           # subu $a0, $a0, $v0, now in seek_nosync
              (0x48d418, 0x03c0a825, 0),           # move $s5, $fp: the probe loop's exit keeps the probed position
              (0x48d41c, 0x02e08825, 0),           # move $s1, $s7
              (0x48dce0, 0x1440fdcd, 0x14400003),  # bnez $v0, 0x48d418 -> 0x48dcf0: no probe, so the estimate's
              (0x48d80c, 0x188002d5, 0))           # blez $a0, 0x48e364: a probe past the target steps back, near the end too
# demux_audio_open's FLAC metadata walk: a length read past the file's end is negative, the walk seeks back by it and, on a
# short file, reads the same blocks forever. It now ends the walk (docs/internals.md#a-flac-cut-inside-its-metadata).
FLAC_META_EOF = ((0x490ba0, 0x06000163, 0x06000028),  # bltz $s0, 0x491130 -> 0x490c44, past the last block's branch
                 (0x4929a4, 0x1000f9e2, 0x1000f8a7))  # b 0x491130 -> b 0x490c44: the last length byte's read at the end
# get-music-info runs on the command thread and reads mpctx->sh_audio, NULL while a song is starting: stock's NULL branch went
# on to read through it and killed the player (docs/internals.md#music-info-between-songs). Now it answers as with no stream.
# ponytail: NULL only, a stream freed after the checks is still read.
MEDIA_INFO_NULL = ((0x42c068, 0x10600008, 0x106000fc),  # beqz $v1, 0x42c08c -> 0x42c45c, the no-playing-status reply
                   (0x42c588, 0x8fa21b20, 0x1060ffb4))  # lw $v0 (dead: 0x42c070 overwrites it) -> beqz $v1, 0x42c45c: the same check on the length call's path; not shown it can fire there
LIBC = {
    'dlsym': ('void *', 'void *, const char *'),
    'memset': ('void *', 'void *, int, unsigned'),
    'memcpy': ('void *', 'void *, const void *, unsigned'),
    'memcmp': ('int', 'const void *, const void *, unsigned'),
    'strcmp': ('int', 'const char *, const char *'),
    'strlen': ('unsigned', 'const char *'),
    'fputs': ('int', 'const char *, void *'),
    'calloc': ('void *', 'unsigned, unsigned'),
    'realloc': ('void *', 'void *, unsigned'),  # addon.c got: a kept reply to its size
    'free': ('void', 'void *'),
    'memmove': ('void *', 'void *, const void *, unsigned'),
    'pow': ('double', 'double, double'), 'cos': ('double', 'double'),
    'sin': ('double', 'double'), 'log': ('double', 'double'),
    'fopen': ('void *', 'const char *, const char *'),
    'fread': ('unsigned', 'void *, unsigned, unsigned, void *'),
    'fwrite': ('unsigned', 'const void *, unsigned, unsigned, void *'),
    'fclose': ('int', 'void *'), 'ferror': ('int', 'void *'),
    'fflush': ('int', 'void *'), 'fileno': ('int', 'void *'), 'fsync': ('int', 'int'),
    'rename': ('int', 'const char *, const char *'), 'unlink': ('int', 'const char *'),
    '__xstat': ('int', 'int, const char *, void *'),
    'access': ('int', 'const char *, int'), 'mkdir': ('int', 'const char *, unsigned'),
    'rmdir': ('int', 'const char *'),  # Apps' Remove (netupdate.c)
    'lseek64': ('long long', 'int, long long, int'), 'read': ('int', 'int, void *, unsigned'),
    'snprintf': ('int', 'char *, unsigned, const char *, ...'),
    'llrint': ('long long', 'double'),
    '__isoc99_sscanf': ('int', 'const char *, const char *, ...'),  # peq.c peq_number: no strtod in demo's GOT
    'opendir': ('void *', 'const char *'), 'closedir': ('int', 'void *'),
    'readdir': ('struct dirent *', 'void *'),
    'qsort': ('void', 'void *, unsigned, unsigned, int (*)(const void *, const void *)'),
    # libcstl and socket imports for the Play/Pause queue menu in navigation.c
    '_create_deque': ('void *', 'const char *'), 'deque_init': ('void', 'void *'),
    'deque_init_copy': ('void', 'void *, const void *'), 'deque_size': ('unsigned', 'const void *'),
    'deque_at': ('void *', 'const void *, unsigned'), '_deque_push_back': ('void', 'void *, ...'),
    'deque_assign': ('void', 'void *, const void *'), 'deque_clear': ('void', 'void *'),
    'deque_pop_back': ('void', 'void *'),
    'deque_destroy': ('void', 'void *'), 'send': ('int', 'int, const void *, unsigned, int'),
    'accept': ('int', 'int, void *, unsigned *'),  # MQA's saved mode before publishing the player socket
    # Coverflow's art thread (coverflow.c)
    'pthread_create': ('int', 'unsigned long *, const void *, void *(*)(void *), void *'),
    'pthread_join': ('int', 'unsigned long, void **'), 'pthread_self': ('unsigned long', 'void'), 'pthread_detach': ('int', 'unsigned long'), 'pthread_mutex_lock': ('int', 'void *'),
    'sem_wait': ('int', 'void *'), 'sem_post': ('int', 'void *'),  # tidal.c: the idle stream threads park on these
    'pthread_mutex_unlock': ('int', 'void *'), 'statfs': ('int', 'const char *, void *'),
    'strdup': ('char *', 'const char *'),
    'strrchr': ('char *', 'const char *, int'), 'strcasecmp': ('int', 'const char *, const char *'),
    'munmap': ('int', 'void *, unsigned'),
    'strcasestr': ('char *', 'const char *, const char *'),
    'strncmp': ('int', 'const char *, const char *, unsigned'),
    'strncasecmp': ('int', 'const char *, const char *, unsigned'),
    # iPod status bar clock (navigation.c)
    'time': ('long', 'long *'),
    'localtime_r': ('const int *', 'const long *, int *'), 'tzset': ('void', 'void'),
    # Scrobble upload (scrobble.c): demo's libcurl and libcrypto
    'fseek': ('int', 'void *, long, int'), 'ftell': ('long', 'void *'), 'atoi': ('int', 'const char *'),
    'strtol': ('long', 'const char *, char **, int'),  # addon.c progress_row
    'strstr': ('char *', 'const char *, const char *'),
    'curl_easy_init': ('void *', 'void'), 'curl_easy_setopt': ('int', 'void *, int, ...'),
    'curl_easy_perform': ('int', 'void *'), 'curl_easy_getinfo': ('int', 'void *, int, ...'),
    'curl_easy_cleanup': ('void', 'void *'), 'curl_slist_append': ('void *', 'void *, const char *'),
    'curl_slist_free_all': ('void', 'void *'), 'MD5_Init': ('int', 'void *'),
    'MD5_Update': ('int', 'void *, const void *, unsigned'), 'MD5_Final': ('int', 'unsigned char *, void *'),
    'dup2': ('int', 'int, int'),
    '__udivdi3': ('unsigned long long', 'unsigned long long, unsigned long long'),
    # Videos (books.c): q2video started and waited for, and its key socket
    'fork': ('int', 'void'), 'execl': ('int', 'const char *, const char *, ...'), 'exit': ('void', 'int'),
    # Home's Rockbox row (navigation.c rockbox_open)
    'system': ('int', 'const char *'),
    'waitpid': ('int', 'int, int *, int'), '__errno_location': ('int *', 'void'), 'socket': ('int', 'int, int, int'), 'close': ('int', 'int'),
    'connect': ('int', 'int, const void *, unsigned'),
    'recv': ('int', 'int, void *, unsigned, int'),
    'select': ('int', 'int, void *, void *, void *, void *'),
    'getsockopt': ('int', 'int, int, int, void *, unsigned *'),
    'fcntl': ('int', 'int, int, ...'), 'ioctl': ('int', 'int, unsigned, ...'),
    'usleep': ('int', 'unsigned'),
    'sendto': ('int', 'int, const void *, unsigned, int, const void *, unsigned'),
    # The visualizer's PCM tap (peq_player.c writes, visualizer.c reads) and its analysis
    'open': ('int', 'const char *, int, ...'), 'ftruncate': ('int', 'int, long'),
    'mmap': ('void *', 'void *, unsigned, int, int, int, long'),
    'mmap64': ('void *', 'void *, unsigned, int, int, int, long long'),
    'clock_gettime': ('int', 'int, void *'),
    'sinf': ('float', 'float'), 'cosf': ('float', 'float'),
    'write': ('int', 'int, const void *, unsigned'),
}

def compile_common(out, binary, player=False, flac_experiments=()):
    got = {}
    for line in run('readelf', '-AW', binary).splitlines():
        words = line.split()
        if len(words) >= 6 and 'UND' in words and re.fullmatch('[0-9a-f]{8}', words[0]):
            got[words[-1].split('@')[0]] = int(words[0], 16)
    gp = 0xb16750 if player else GP
    header = ['struct dirent { unsigned ino, off; unsigned short reclen; unsigned char d_type; char d_name[256]; };']
    asm = ['.set noreorder', '.text']
    for name, (ret, args) in LIBC.items():
        # Unused I/O is removed by --gc-sections in the player payload.
        alias = {'fopen': 'fopen64', 'readdir': 'readdir64', 'pow': '__pow_finite'}.get(name, name) if player else name
        if name == 'localtime_r' and not player:
            libc = symbols(out/'libc-2.28.so')
            delta = libc['localtime_r@@GLIBC_2.0'] - libc['tzset@@GLIBC_2.0']
            check(delta == -7724, 'Unexpected libc localtime_r offset')
            header.append(f'extern {ret} peq_lib_{name}({args});')
            header.append(f'#define {name} peq_lib_{name}')
            address = got['tzset']
            asm += ['.section .scratch,"aw",@nobits', '.align 2',
                    'localtime_r_target: .space 4', f'.section .text.peq_lib_{name},"ax",@progbits',
                    f'.globl peq_lib_{name}', f'peq_lib_{name}:',
                    'lui $t8, %hi(localtime_r_target)', 'lw $t9, %lo(localtime_r_target)($t8)',
                    'bnez $t9, 1f', 'nop',
                    # Resolve tzset's lazy GOT entry once: unlike localtime, it writes no shared buffer.
                    'addiu $sp, $sp, -32', 'sw $ra, 28($sp)', 'sw $a0, 20($sp)', 'sw $a1, 24($sp)',
                    'jal peq_lib_tzset', 'nop', 'lw $ra, 28($sp)', 'lw $a0, 20($sp)',
                    'lw $a1, 24($sp)', 'addiu $sp, $sp, 32',
                    f'lui $t8, {address >> 16}', f'ori $t8, $t8, {address & 65535}',
                    'lw $t9, 0($t8)', f'addiu $t9, $t9, {delta}',
                    'lui $t8, %hi(localtime_r_target)', 'sw $t9, %lo(localtime_r_target)($t8)',
                    '1:', 'jr $t9', 'nop']
            continue
        if alias not in got:
            header.append(f'extern {ret} {name}({args});')
            continue
        address = got[alias]
        header += [f'extern {ret} peq_lib_{name}({args});', f'#define {name} peq_lib_{name}']
        asm += [f'.section .text.peq_lib_{name},"ax",@progbits', f'.globl peq_lib_{name}',
                f'peq_lib_{name}:', f'lui $gp, {gp >> 16}', f'ori $gp, $gp, {gp & 65535}',
                f'lui $t8, {address >> 16}', f'ori $t8, $t8, {address & 65535}',
                'lw $t9, 0($t8)', 'jr $t9', 'nop']
        if name in ('memcpy', 'memset', '__udivdi3'):
            asm += [f'.globl {name}', f'.set {name}, peq_lib_{name}']
    if not player:
        syms = symbols(binary) | PRIVATE_FUNCTIONS
        for name, (ret, args) in FUNCTIONS.items():
            if name in syms and syms[name]:
                header.append(f'#define {name} (({ret} (*)({args}))0x{syms[name]:x}u)')
        # build.py has already checked each is a one-byte global.
        header += [f'#define {name} (*(volatile unsigned char *)0x{syms[name]:x}u)' for name in GLOBALS]
    else:
        # filter_n_bytes keeps sh_audio in $fp; af_play's two arguments stay intact.
        asm += ['.section .text.mp3_filter_entry,"ax",@progbits', '.globl mp3_filter_entry',
                'mp3_filter_entry:', 'move $a2, $fp', 'j mp3_filter', 'nop']
    (out/'peq_platform.h').write_text('\n'.join(header)+'\n')
    (out/'peq_imports.S').write_text('\n'.join(asm)+'\n')
    flags = [*FLAGS, '-fno-math-errno', '-ffunction-sections', '-fdata-sections']
    if player: flags += ['-mnan=2008']
    objects = []
    for name in (['peq.c', 'peq_player.c', 'mp3_gapless.c', 'lavf_gapless.c'] + [f'flac_{n}.c' for n in flac_experiments]) if player else ['peq.c', 'peq_ui.c', 'coverflow.c', 'library_scope.c', 'scrobble.c', 'photos.c', 'books.c', 'games.c', 'visualizer.c', 'playback.c', 'upnext.c', 'spotify.c', 'addon.c', 'addon_library.c', 'radio.c', 'tidal.c', 'netupdate.c', 'onlinematch.c', 'crossfade.c', 'nightshift.c', 'headset.c', 'lockscreen.c', 'chapters.c', 'lyrics.c']:
        obj = out/(name+'.o')
        run('clang', *flags, '-I', out, '-c', ROOT/'patch'/name, '-o', obj)
        objects.append(obj)
    obj = out/'peq_imports.o'
    run('clang', *flags, '-c', out/'peq_imports.S', '-o', obj)
    return [*objects, obj]

def patch_player(raw, out, flac_experiments=()):
    from flac import selection, EXPORTS, patch_words
    flac_experiments = selection(flac_experiments)
    check(sha(raw) == PLAYER_SHA, 'Unexpected hciplayer')
    out.mkdir(exist_ok=True)
    binary = out/'stock-hciplayer'
    binary.write_bytes(raw)
    check(raw[fileoff(raw, EQ_DESC):fileoff(raw, EQ_DESC)+24].hex() ==
          'ac3d8900cc6388003c3b890098c988000100000024014500', 'EQ descriptor mismatch')
    objects = compile_common(out, binary, True, flac_experiments=flac_experiments)
    script = out/'link.ld'
    script.write_text('SECTIONS { . = 0xe10000; .text : { *(.text*) } .rodata : { *(.rodata*) } '
                      '.data : { *(.data*) *(.sdata*) } .bss : { *(.bss*) *(.sbss*) } '
                      '__end = .; /DISCARD/ : { *(.comment) *(.note*) *(.pdr) *(.mdebug*) '
                      '*(.MIPS.abiflags) *(.reginfo) } }')
    run('ld.lld', '-m', 'elf32ltsmip', '--gc-sections', '-T', script, '-e', 'peq_open', '--undefined=mp3_seek', '--undefined=id3_gain',
        '--undefined=mp3_private_alloc', '--undefined=mp3_filter_entry', '--undefined=mp3_close', '--undefined=mp3_read', '--undefined=mp3_fill',
        '--undefined=hci_idle_wait',
        '--undefined=hci_pause_wait', '--undefined=hci_command', '--undefined=rs_open', '--undefined=ao_init', '--undefined=ao_uninit',
        *(f'--undefined={name}' for _, _, name in (*DAC_RELEASE, *PROBE_STOP, *STOP_SITES, STOP_FILL, *PRELOAD_CLOSE, FORCED_EOF, EXIT_HOLD, PRELOAD_PUBLISH, PRELOAD_FAILED, COLD_CLEAR, FLAC_GATE, SEEK_NOSYNC)),
        *(f'--undefined={symbol}' for n in flac_experiments for symbol in EXPORTS[n]),
        *objects, '-o', out/'peq.elf')
    run('llvm-objcopy', '-O', 'binary', out/'peq.elf', out/'peq.bin')
    ps = symbols(out/'peq.elf')
    payload = (out/'peq.bin').read_bytes()
    data = bytearray(raw)
    off = fileoff(raw, EQ_OPEN_SLOT)
    data[off:off+4] = struct.pack('<I', ps['peq_open'])
    patch_word(data, VBR_SCAN, 0x144008a8, 0, 'VBR scan')  # bnez $v0, 0x491de0 -> nop
    patch_word(data, *PAUSE_WAIT, jal(ps['hci_pause_wait']), 'pause loop waits for a command')
    patch_word(data, *CMD_DONE, jal(ps['hci_command']), 'a command wakes the idle and pause loops')
    patch_word(data, *IDLE_WAIT, jal(ps['hci_idle_wait']), 'idle loop waits on the play flag')
    for address, stock, patched in EQ_RATE_GATES:
        patch_word(data, address, stock, patched, 'Equalizer rate gate')
    for address, stock, patched in (*MQA_GATE, *MQA_PROBE):
        patch_word(data, address, stock, patched, 'MQA side chain')
    for address, stock, patched in USB_BUFFER:
        patch_word(data, address, stock, patched, 'USB DAC buffer')
    for address, stock, name in DAC_RELEASE:
        patch_word(data, address, stock, jal(ps[name]), 'DAC release wait')
    for address, stock, name in PROBE_STOP:
        patch_word(data, address, stock, j(ps[name]), 'Probe stop')
    patch_word(data, PRELOAD_PUBLISH[0], PRELOAD_PUBLISH[1], j(ps[PRELOAD_PUBLISH[2]]), 'Preload publish')
    patch_word(data, PRELOAD_FAILED[0], PRELOAD_FAILED[1], jal(ps[PRELOAD_FAILED[2]]), 'Preload failed')
    for address, stock, patched in PRELOAD_FAIL_STORES:
        patch_word(data, address, stock, patched, 'Preload failed')
    patch_word(data, FORCED_EOF[0], FORCED_EOF[1], jal(ps[FORCED_EOF[2]]), 'Forced EOF')
    patch_word(data, EXIT_HOLD[0], EXIT_HOLD[1], jal(ps[EXIT_HOLD[2]]), 'Exit hold')
    for address, stock, name in STOP_SITES:
        patch_word(data, address, stock, jal(ps[name]), 'Stop base')
    for address, stock, patched in STOP_NOPS:
        patch_word(data, address, stock, patched, 'Stop base')
    patch_word(data, STOP_FILL[0], STOP_FILL[1], j(ps[STOP_FILL[2]]), 'Stop in a fill')
    for address, stock, name in PRELOAD_CLOSE:
        patch_word(data, address, stock, j(ps[name]), 'Preload close')
    patch_word(data, COLD_CLEAR[0], COLD_CLEAR[1], jal(ps[COLD_CLEAR[2]]), 'Cold clear')
    patch_word(data, FLAC_GATE[0], FLAC_GATE[1], j(ps[FLAC_GATE[2]]), 'FLAC gate')
    patch_word(data, SEEK_NOSYNC[0], SEEK_NOSYNC[1], j(ps[SEEK_NOSYNC[2]]), 'Seek no sync')
    for address, stock, patched in SEEK_WORDS:
        patch_word(data, address, stock, patched, 'Seek no sync')
    for address, stock, patched in FLAC_META_EOF:
        patch_word(data, address, stock, patched, 'FLAC metadata at EOF')
    for address, stock, patched in MEDIA_INFO_NULL:
        patch_word(data, address, stock, patched, 'Music info with no stream')
    patch_word(data, SEEK_SLOT, STOCK_SEEK, ps['mp3_seek'], 'Audio demuxer seek slot')
    patch_word(data, RS_OPEN_SLOT, RS_OPEN, ps['rs_open'], 'Resampler open slot')
    patch_word(data, AO_INIT_SLOT, AO_INIT, ps['ao_init'], 'Output init slot')
    patch_word(data, AO_UNINIT_SLOT, AO_UNINIT, ps['ao_uninit'], 'Output uninit slot')
    patch_word(data, MP3_ALLOC, jal(0xadaf00), jal(ps['mp3_private_alloc']), 'MP3 gapless private state')
    patch_word(data, MP3_FILTER, jal(0x44d350), jal(ps['mp3_filter_entry']), 'MP3 gapless PCM')
    patch_word(data, MP3_CLOSE, 0x48b7e4, ps['mp3_close'], 'MP3 gapless cleanup')
    patch_word(data, MP3_READ, jal(DEMUX_READ), jal(ps['mp3_read']), 'MP3 last frame')
    patch_word(data, MP3_FILL, DEMUX_FILL, ps['mp3_fill'], 'MP3 tail tags')
    for address in ID3_CALLS:
        patch_word(data, address, jal(STOCK_ID3), jal(ps['id3_gain']), 'MP3 ReplayGain')
    patch_word(data, *LAVF_PRIV, 'lavf gapless window')
    patch_word(data, *MOV_EDITS, 'M4A edit list kept')
    for address, stock, patched in patch_words(flac_experiments, ps):
        patch_word(data, address, stock, patched, 'Experimental FLAC')
    append_payload(data, payload, PLAYER_BASE, max(len(payload), ps['__end']-PLAYER_BASE), 'player')
    (out/'hciplayer').write_bytes(data)
    return dict(stock_sha256=PLAYER_SHA, sha256=sha(data), payload_sha256=sha(payload),
                **({'flac_experiments': list(flac_experiments)} if flac_experiments else {}))
